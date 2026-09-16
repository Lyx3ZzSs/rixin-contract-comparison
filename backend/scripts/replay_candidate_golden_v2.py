from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings  # noqa: E402
from app.infrastructure.artifact_store import ArtifactStore  # noqa: E402
from app.infrastructure.task_repository import SQLiteTaskRepository  # noqa: E402
from app.models import Document  # noqa: E402
from app.services.compare_service import CompareService  # noqa: E402
from app.services.extractors.base import ExtractionResult  # noqa: E402


REPO_ROOT = BACKEND_DIR.parent
FIXTURE_ROOT = REPO_ROOT / "backend" / "tests" / "fixtures" / "candidate_golden_v2"


class _CachedCandidateExtractor:
    """Replay reviewed OCR snapshots without depending on mutable remote OCR."""

    name = "ppstructure_ocr_hybrid"

    def __init__(self, raw_by_filename: dict[str, Path]) -> None:
        self.raw_by_filename = raw_by_filename

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        source = Path(path)
        raw_path = self.raw_by_filename.get(source.name)
        if raw_path is None:
            raise RuntimeError(f"cached candidate extraction not found: {source.name}")
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        document = Document.model_validate(payload["document"])
        document.path = str(source)
        document.filename = source.name
        return ExtractionResult(
            document=document,
            extractor_used="ppstructure_ocr_hybrid",
            raw_result_path=str(raw_path),
        )


def _cached_extractions(manifest: dict[str, Any]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for spec in manifest["contracts"]:
        raw_dir = REPO_ROOT / "storage" / "tasks" / spec["source_task_id"] / "diagnostics" / "ocr"
        for raw_path in raw_dir.glob("*_ppstructure_ocr_hybrid_raw.json"):
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
            filename = str((payload.get("document") or {}).get("filename") or "")
            if filename:
                result[filename] = raw_path
    return result


def _norm(value: Any) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _match_expected_text(
    case: dict[str, Any], diffs: list[dict[str, Any]], used: set[int],
) -> tuple[int, dict[str, Any]] | None:
    fragments = case.get("expected_compare_fragments") or []
    if not fragments:
        return None
    pages = set(case.get("expected_pages") or [])
    for index, diff in enumerate(diffs):
        if index in used or diff.get("diff_type") != case.get("expected_diff_type", "ADD"):
            continue
        display = _norm(diff.get("compare_snippet") or diff.get("readable_change"))
        evidence_pages = {item.get("page_no") for item in diff.get("compare_evidence", [])}
        if all(_norm(fragment) in display for fragment in fragments) and (not pages or pages <= evidence_pages):
            return index, diff
    return None


def _page(diff: dict[str, Any]) -> int | None:
    for side in ("compare_evidence", "original_evidence"):
        evidence = diff.get(side) or []
        if evidence:
            return int(evidence[0]["page_no"])
    return None


def _decision(diff: dict[str, Any]) -> str:
    return str((diff.get("match_score_details") or {}).get("change_decision") or "")


def _is_confirmed(diff: dict[str, Any]) -> bool:
    return _decision(diff) == "CONFIRMED" or (
        _decision(diff) not in {"NEEDS_REVIEW", "CANDIDATE"}
        and diff.get("quality_status") != "NEEDS_REVIEW"
        and diff.get("content_recognition_status") != "NEEDS_REVIEW"
    )


def _supporting_pages(diff: dict[str, Any]) -> set[int]:
    details = diff.get("match_score_details") or {}
    pages = {int(value) for value in details.get("supporting_pages") or []}
    for side in ("compare_evidence", "original_evidence"):
        pages.update(int(item["page_no"]) for item in diff.get(side) or [] if item.get("page_no") is not None)
    return pages


def _match_expected_visual(
    case: dict[str, Any],
    after: list[dict[str, Any]],
    used: set[int],
    *,
    preferred_diff_id: str | None = None,
) -> tuple[int, dict[str, Any]] | None:
    expected_type = case.get("expected_object_type")
    expected_party = case.get("expected_party_role")
    expected_pages = {int(value) for value in case.get("expected_pages") or [case["page"]]}
    candidates: list[tuple[int, dict[str, Any], int]] = []
    for index, item in enumerate(after):
        if index in used and expected_type != "EDGE_SEAM_SEAL":
            continue
        details = item.get("match_score_details") or {}
        if details.get("object_type") != expected_type:
            continue
        if expected_party is not None and details.get("party_role") != expected_party:
            continue
        page_overlap = len(expected_pages & _supporting_pages(item))
        minimum_overlap = 2 if expected_type == "EDGE_SEAM_SEAL" and len(expected_pages) > 1 else 1
        if page_overlap >= minimum_overlap:
            candidates.append((index, item, page_overlap))
    if not candidates:
        return None
    index, item, _overlap = max(
        candidates,
        key=lambda candidate: (
            candidate[1].get("diff_id") == preferred_diff_id,
            candidate[2],
            _is_confirmed(candidate[1]),
        ),
    )
    return index, item


def _load_before(task_id: str) -> list[dict[str, Any]]:
    database = REPO_ROOT / "storage" / "tasks.sqlite3"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT details_json FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError(f"candidate source task not found: {task_id}")
    return list(json.loads(row[0]).get("diffs") or [])


def _similarity(before: dict[str, Any], after: dict[str, Any]) -> float:
    score = 0.0
    if before.get("source_type") == after.get("source_type"):
        score += 3.0
    if before.get("diff_type") == after.get("diff_type"):
        score += 1.0
    if _page(before) == _page(after):
        score += 2.0
    for key in ("title", "original_text", "compare_text", "readable_change"):
        score += SequenceMatcher(None, _norm(before.get(key)), _norm(after.get(key))).ratio()
    return score


def _match_after(
    before: dict[str, Any], after: list[dict[str, Any]], used: set[int]
) -> tuple[int, dict[str, Any]] | None:
    before_page = _page(before)
    candidates = []
    for index, item in enumerate(after):
        if index in used:
            continue
        same_source = before.get("source_type") == item.get("source_type")
        aligned_page_candidate = bool(
            before.get("source_type") == "page"
            and item.get("source_type") == "table"
            and (item.get("match_score_details") or {}).get("decision_reason")
            == "ALIGNED_PAGE_STRUCTURE_NOT_EXTRACTED"
        )
        if not same_source and not aligned_page_candidate:
            continue
        if before.get("diff_type") != item.get("diff_type"):
            continue
        after_page = _page(item)
        if before_page is not None and after_page is not None and abs(before_page - after_page) > 2:
            continue
        candidates.append((index, item))
    if not candidates:
        return None
    index, item = max(candidates, key=lambda pair: _similarity(before, pair[1]))
    return (index, item) if _similarity(before, item) >= 5.0 else None


def replay(
    output_path: Path | None = None,
    contract_keys: set[str] | None = None,
) -> dict[str, Any]:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    oracle = json.loads((FIXTURE_ROOT / "oracle.json").read_text(encoding="utf-8"))
    cases_by_contract: dict[str, list[dict[str, Any]]] = {}
    for case in oracle["cases"]:
        cases_by_contract.setdefault(case["contract_key"], []).append(case)

    report: dict[str, Any] = {"oracle_version": oracle["oracle_version"], "contracts": []}
    totals = Counter()
    with tempfile.TemporaryDirectory(prefix="candidate-golden-v2-") as runtime_dir:
        settings = Settings(storage_dir=Path(runtime_dir))
        extractor = _CachedCandidateExtractor(_cached_extractions(manifest))
        service = CompareService(
            extractor=extractor,
            structured_extractor=extractor,
            repository=SQLiteTaskRepository(settings),
            artifact_store=ArtifactStore(settings),
        )
        for spec in manifest["contracts"]:
            key = spec["contract_key"]
            if contract_keys and key not in contract_keys:
                continue
            before = _load_before(spec["source_task_id"])
            task = service.compare(
                REPO_ROOT / spec["original"],
                REPO_ROOT / spec["compare"],
                task_id=f"candidate-v2-{key.lower()}",
            )
            after = [item.model_dump(mode="json") for item in task.diffs]
            before_by_id = {item["diff_id"]: item for item in before}
            used: set[int] = set()
            changes: list[dict[str, Any]] = []
            ordered_cases = sorted(
                cases_by_contract[key],
                key=lambda case: (
                    0
                    if case.get("phase3_target")
                    else 1
                    if case["classification"] == "TRUE_POSITIVE" and case.get("expected_object_type")
                    else 2
                    if case["classification"] == "TRUE_POSITIVE"
                    else 3
                ),
            )
            for case in ordered_cases:
                before_id = case.get("before_diff_id")
                if not before_id:
                    matched = (_match_expected_text(case, after, used) if case.get("expected_compare_fragments")
                               else _match_expected_visual(case, after, used) if case.get("phase3_target") else None)
                    if matched is None:
                        status = (
                            "PHASE3_TARGET_STILL_MISSING"
                            if case.get("phase3_target") or case.get("expected_compare_fragments")
                            else "KNOWN_FALSE_NEGATIVE_NOT_TARGETED"
                        )
                        after_diff = None
                        totals["false_negative_remaining"] += 1
                    else:
                        index, after_diff = matched
                        used.add(index)
                        if _is_confirmed(after_diff):
                            status = "RECOVERED_CONFIRMED"
                            totals["false_negative_recovered_confirmed"] += 1
                        else:
                            status = "RECOVERED_REVIEW"
                            totals["false_negative_recovered_review"] += 1
                    changes.append(
                        {
                            "case_id": case["case_id"],
                            "classification": "FALSE_NEGATIVE",
                            "status": status,
                            "after_diff_id": after_diff.get("diff_id") if after_diff else None,
                            "after_decision": _decision(after_diff) if after_diff else None,
                            "after_quality_status": after_diff.get("quality_status") if after_diff else None,
                            "after_match_score_details": after_diff.get("match_score_details") if after_diff else None,
                        }
                    )
                    continue
                before_diff = before_by_id[before_id]
                matched = (
                    _match_expected_visual(case, after, used, preferred_diff_id=before_id)
                    if case.get("expected_object_type")
                    else _match_after(before_diff, after, used)
                )
                if matched is None:
                    status = "MISSING_AFTER"
                    after_diff = None
                else:
                    index, after_diff = matched
                    used.add(index)
                    if _is_confirmed(before_diff) and not _is_confirmed(after_diff):
                        status = "DOWNGRADED_TO_REVIEW"
                    elif not _is_confirmed(before_diff) and _is_confirmed(after_diff):
                        status = "UPGRADED_TO_CONFIRMED"
                    else:
                        status = "PRESERVED"
                classification = case["classification"]
                if classification == "TRUE_POSITIVE":
                    if matched is None:
                        totals["true_positive_missing"] += 1
                    else:
                        totals["true_positive_preserved"] += 1
                        if after_diff is not None and _is_confirmed(after_diff):
                            totals["true_positive_confirmed"] += 1
                elif classification == "FALSE_POSITIVE":
                    if matched is None:
                        totals["false_positive_removed"] += 1
                    elif after_diff is not None and not _is_confirmed(after_diff):
                        totals["false_positive_review"] += 1
                    else:
                        totals["false_positive_confirmed"] += 1
                changes.append(
                    {
                        "case_id": case["case_id"],
                        "classification": classification,
                        "before_diff_id": before_id,
                        "after_diff_id": after_diff.get("diff_id") if after_diff else None,
                        "status": status,
                        "before_decision": _decision(before_diff),
                        "after_decision": _decision(after_diff) if after_diff else None,
                        "after_quality_status": after_diff.get("quality_status") if after_diff else None,
                        "observation_reason": (
                            (after_diff.get("match_score_details") or {}).get("decision_reason") if after_diff else None
                        ),
                        "after_match_score_details": after_diff.get("match_score_details") if after_diff else None,
                    }
                )
            unmatched_after = [
                {
                    "diff_id": item.get("diff_id"),
                    "source_type": item.get("source_type"),
                    "diff_type": item.get("diff_type"),
                    "title": item.get("title"),
                    "original_text": item.get("original_text"),
                    "compare_text": item.get("compare_text"),
                    "quality_status": item.get("quality_status"),
                    "match_score_details": item.get("match_score_details"),
                }
                for index, item in enumerate(after)
                if index not in used
            ]
            totals["new_after_diff_count"] += len(unmatched_after)
            totals["new_after_confirmed_count"] += sum(_is_confirmed(item) for item in unmatched_after)
            report["contracts"].append(
                {
                    "contract_key": key,
                    "before_diff_count": len(before),
                    "after_diff_count": len(after),
                    "cases": changes,
                    "unmatched_after": unmatched_after,
                }
            )
            print(f"CANDIDATE REPLAY: {key} before={len(before)} after={len(after)}")

    report["totals"] = dict(totals)
    if output_path is not None:
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("CANDIDATE TOTALS: " + json.dumps(report["totals"], ensure_ascii=False, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--contract", action="append", dest="contracts")
    args = parser.parse_args()
    replay(args.output, set(args.contracts or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
