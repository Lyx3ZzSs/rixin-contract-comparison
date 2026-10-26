from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "contract_golden"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
DATA_ROOT_ENV = "GOLDEN_CONTRACT_DATA_ROOT"
EXPECTED_BASELINE_VERSION = "contract-golden-v3"
EXPECTED_CONTRACT_COUNT = 15
EXPECTED_ASSERTION_COUNT = 318


class GoldenDataError(AssertionError):
    pass


@dataclass(frozen=True)
class GoldenContract:
    contract_key: str
    display_name: str
    source_run_id: str
    original_pdf: Path
    compare_pdf: Path
    original_filename: str
    compare_filename: str
    oracle: dict[str, Any]


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        raise GoldenDataError(f"GOLDEN DATA ERROR: manifest missing: {MANIFEST_PATH}")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    contracts = manifest.get("contracts") or []
    expected = int(manifest.get("expected_contract_count") or 0)
    contract_count = int(manifest.get("contract_count") or 0)
    assertion_count = int(manifest.get("assertion_count") or 0)
    if manifest.get("baseline_version") != EXPECTED_BASELINE_VERSION:
        raise GoldenDataError(
            "GOLDEN DATA ERROR: baseline version mismatch: "
            f"expected={EXPECTED_BASELINE_VERSION} actual={manifest.get('baseline_version')}"
        )
    if not str(manifest.get("created_at") or "").strip():
        raise GoldenDataError("GOLDEN DATA ERROR: manifest created_at is required")
    if contract_count != EXPECTED_CONTRACT_COUNT or assertion_count != EXPECTED_ASSERTION_COUNT:
        raise GoldenDataError(
            "GOLDEN DATA ERROR: sealed baseline counts changed: "
            f"contracts={contract_count} assertions={assertion_count}"
        )
    if expected != EXPECTED_CONTRACT_COUNT or len(contracts) != expected:
        raise GoldenDataError(
            f"GOLDEN DATA ERROR: expected {EXPECTED_CONTRACT_COUNT} manifest contracts, "
            f"declared={expected}, loaded={len(contracts)}"
        )
    keys = [str(item.get("contract_key") or "") for item in contracts]
    if len(set(keys)) != len(keys) or any(not key for key in keys):
        raise GoldenDataError("GOLDEN DATA ERROR: contract_key values must be unique and non-empty")
    return manifest


def resolve_data_root(data_root: str | Path | None = None) -> Path:
    configured = str(data_root or os.environ.get(DATA_ROOT_ENV) or "").strip()
    if not configured:
        raise GoldenDataError(
            f"GOLDEN DATA ERROR: {DATA_ROOT_ENV} is required; blocking Golden tests never skip"
        )
    root = Path(configured).expanduser().resolve()
    if not root.is_dir():
        raise GoldenDataError(f"GOLDEN DATA ERROR: data root does not exist: {root}")
    return root


def load_corpus(data_root: str | Path | None = None) -> list[GoldenContract]:
    manifest = load_manifest()
    root = resolve_data_root(data_root)
    loaded: list[GoldenContract] = []
    for entry in manifest["contracts"]:
        original = _verify_pdf(root, entry["original"], contract_key=entry["contract_key"], side="original")
        compare = _verify_pdf(root, entry["compare"], contract_key=entry["contract_key"], side="compare")
        oracle_path = FIXTURE_ROOT / str(entry["oracle"])
        if not oracle_path.is_file():
            raise GoldenDataError(f"GOLDEN DATA ERROR: oracle missing: {oracle_path}")
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        if oracle.get("contract_key") != entry["contract_key"]:
            raise GoldenDataError(
                f"GOLDEN DATA ERROR: oracle contract mismatch: {oracle_path}"
            )
        assertions = oracle.get("assertions") or []
        if not assertions:
            raise GoldenDataError(f"GOLDEN DATA ERROR: oracle has no assertions: {oracle_path}")
        case_ids = [str(item.get("case_id") or "") for item in assertions]
        if len(set(case_ids)) != len(case_ids) or any(not case_id for case_id in case_ids):
            raise GoldenDataError(f"GOLDEN DATA ERROR: duplicate/blank case_id in {oracle_path}")
        loaded.append(
            GoldenContract(
                contract_key=entry["contract_key"],
                display_name=entry["display_name"],
                source_run_id=entry.get("source_run_id", ""),
                original_pdf=original,
                compare_pdf=compare,
                original_filename=entry["original"].get("source_filename") or original.name,
                compare_filename=entry["compare"].get("source_filename") or compare.name,
                oracle=oracle,
            )
        )
    if len(loaded) != EXPECTED_CONTRACT_COUNT:
        raise GoldenDataError(
            f"GOLDEN DATA ERROR: {EXPECTED_CONTRACT_COUNT} expected, {len(loaded)} loaded"
        )
    return loaded


def oracle_statistics(corpus: list[GoldenContract]) -> dict[str, int]:
    stats = {
        "contract_count": len(corpus),
        "must_exist": 0,
        "must_not_exist": 0,
        "must_not_confirm": 0,
        "must_review": 0,
        "visual_positive": 0,
        "text_positive": 0,
        "expected_case_count": 0,
    }
    for contract in corpus:
        for assertion in contract.oracle["assertions"]:
            kind = str(assertion["assertion"])
            stats["expected_case_count"] += 1
            key = {
                "MUST_EXIST": "must_exist",
                "MUST_NOT_EXIST": "must_not_exist",
                "MUST_NOT_CONFIRM": "must_not_confirm",
                "MUST_REVIEW": "must_review",
            }.get(kind)
            if key is None:
                raise GoldenDataError(f"GOLDEN DATA ERROR: unsupported assertion {kind}")
            stats[key] += 1
            if kind in {"MUST_EXIST", "MUST_REVIEW"}:
                target = "visual_positive" if assertion.get("visual_object_type") else "text_positive"
                stats[target] += 1
    expected_total = sum(int(item.get("expected_case_count") or 0) for item in (c.oracle for c in corpus))
    if expected_total != stats["expected_case_count"]:
        raise GoldenDataError(
            "GOLDEN DATA ERROR: expected case-count sanity check failed: "
            f"declared={expected_total}, loaded={stats['expected_case_count']}"
        )
    manifest_assertion_count = int(load_manifest()["assertion_count"])
    if stats["expected_case_count"] != manifest_assertion_count:
        raise GoldenDataError(
            "GOLDEN DATA ERROR: sealed assertion count mismatch: "
            f"manifest={manifest_assertion_count} loaded={stats['expected_case_count']}"
        )
    if (
        stats["contract_count"] != EXPECTED_CONTRACT_COUNT
        or stats["must_exist"] <= 0
        or stats["must_not_exist"] <= 0
    ):
        raise GoldenDataError(f"GOLDEN DATA ERROR: invalid oracle statistics: {stats}")
    return stats


def validate_execution(expected: list[str], loaded: list[str], executed: list[str]) -> None:
    if len(expected) != EXPECTED_CONTRACT_COUNT or len(set(expected)) != EXPECTED_CONTRACT_COUNT:
        raise GoldenDataError(
            f"GOLDEN EXECUTION ERROR: expected corpus must contain "
            f"{EXPECTED_CONTRACT_COUNT} unique contracts: {expected}"
        )
    if loaded != expected:
        raise GoldenDataError(f"GOLDEN EXECUTION ERROR: loaded corpus mismatch: expected={expected} loaded={loaded}")
    if executed != expected:
        raise GoldenDataError(
            f"GOLDEN EXECUTION ERROR: all {EXPECTED_CONTRACT_COUNT} contracts must execute in manifest order: "
            f"expected={expected} executed={executed}"
        )


def _verify_pdf(root: Path, spec: dict[str, Any], *, contract_key: str, side: str) -> Path:
    root = root.resolve()
    path = (root / str(spec["file"])).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GoldenDataError(f"GOLDEN DATA ERROR: path escapes data root: {path}") from exc
    if not path.is_file():
        raise GoldenDataError(f"GOLDEN DATA ERROR: {contract_key} {side} PDF missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != spec["sha256"]:
        raise GoldenDataError(
            f"GOLDEN DATA ERROR: {contract_key} {side} SHA256 mismatch: expected={spec['sha256']} actual={digest}"
        )
    try:
        with fitz.open(path) as document:
            page_count = len(document)
    except Exception as exc:
        raise GoldenDataError(f"GOLDEN DATA ERROR: cannot read PDF {path}: {exc}") from exc
    if page_count != int(spec["pages"]):
        raise GoldenDataError(
            f"GOLDEN DATA ERROR: {contract_key} {side} page mismatch: expected={spec['pages']} actual={page_count}"
        )
    return path
