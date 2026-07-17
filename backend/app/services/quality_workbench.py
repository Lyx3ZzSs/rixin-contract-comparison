from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.infrastructure.atomic_files import atomic_write_json, atomic_write_text
from app.models import DiffItem
from app.services.diff_quality import DiffQualityProcessor
from scripts.export_ocr_compare_gold_case import export_gold_case
from scripts.evaluate_ocr_compare_quality import evaluate_case_root
from scripts.run_quality_regression import run_regression


SAFE_ID_RE = r"^[A-Za-z0-9_.-]+$"
EXPECTED_DIFF_ALLOWED_FIELDS = {
    "diff_type",
    "source_type",
    "title_contains",
    "original_contains",
    "compare_contains",
    "review_status",
    "reviewer",
    "reviewed_at",
    "severity",
    "notes",
    "false_positive_reason",
    "false_negative_reason",
    "should_not_match_again",
    "source_actual_diff_id",
    "expected_evidence",
}


class QualityWorkbenchError(Exception):
    """Base error for quality workbench operations."""


class InvalidQualityWorkbenchIdError(QualityWorkbenchError):
    """Raised when a case id is not safe to use as a path segment."""


class QualityCaseNotFoundError(QualityWorkbenchError):
    """Raised when a requested quality case does not exist."""


class QualityTaskNotFoundError(QualityWorkbenchError):
    """Raised when a requested task export source does not exist."""


class QualityExpectedDiffNotFoundError(QualityWorkbenchError):
    """Raised when a requested expected diff entry does not exist."""


class QualityCaseInvalidError(QualityWorkbenchError):
    """Raised when a quality case or seed manifest is malformed."""

    error_code = "QUALITY_CASE_INVALID"


class QualityCasesPathConflictError(QualityWorkbenchError):
    """Raised when seed and target paths overlap after resolution."""

    error_code = "QUALITY_CASES_PATH_CONFLICT"


class QualityWorkbenchService:
    def __init__(
        self,
        case_root: Path,
        task_root: Path,
        output_root: Path,
        seed_root: Path | None = None,
    ) -> None:
        self.case_root = _resolve_path(case_root)
        self.task_root = _resolve_path(task_root)
        self.output_root = _resolve_path(output_root)
        self.seed_root = _resolve_path(seed_root) if seed_root is not None else None
        if self.seed_root is not None:
            ensure_quality_paths_separated(self.case_root, self.seed_root)

    def list_cases(self) -> list[dict[str, Any]]:
        if not self.case_root.exists():
            return []

        cases = []
        for case_dir in sorted(self.case_root.iterdir()):
            if not case_dir.is_dir() or not (case_dir / "expected.json").exists():
                continue
            expected = _read_expected_json(case_dir / "expected.json")
            actual = _read_json(case_dir / "actual.json") if (case_dir / "actual.json").exists() else {}
            cases.append(self._build_summary(case_dir, expected, actual))
        return cases

    def get_case(self, case_id: str) -> dict[str, Any]:
        case_dir = self._case_dir(case_id)
        expected_path = case_dir / "expected.json"
        if not expected_path.exists():
            raise QualityCaseNotFoundError(f"Quality case not found: {case_id}")

        expected = _read_expected_json(expected_path)
        actual_path = case_dir / "actual.json"
        actual = _read_json(actual_path) if actual_path.exists() else {}
        readme_path = case_dir / "README.md"

        return {
            "summary": self._build_summary(case_dir, expected, actual),
            "readme": (readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""),
            "expected": expected,
            "actual_diffs": [_summarize_actual_diff(diff) for diff in actual.get("diffs", [])],
        }

    def export_case(
        self,
        task_id: str,
        case_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        if self.seed_root is not None:
            ensure_quality_paths_separated(self.case_root, self.seed_root)
        task_dir = self._task_dir(task_id)
        case_dir = self._case_dir(case_id)
        if not (task_dir / "task.json").exists():
            raise QualityTaskNotFoundError(f"Quality task not found: {task_id}")
        if case_dir.exists():
            raise FileExistsError(f"Quality case already exists: {case_id}")

        self.case_root.mkdir(parents=True, exist_ok=True)
        staging_root = Path(
            tempfile.mkdtemp(
                prefix=f".{case_id}.",
                suffix=".staging",
                dir=self.case_root,
            )
        )
        staging_case = staging_root / case_id
        try:
            summary = export_gold_case(task_dir, staging_case, force=False)
            _read_expected_json(staging_case / "expected.json")
            _fsync_tree(staging_case)
            publish_directory_without_overwrite(staging_case, case_dir)
            _fsync_directory(self.case_root)
            return summary
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    def review_task(self, task_id: str) -> dict[str, Any]:
        task_dir = self._task_dir(task_id)
        task_path = task_dir / "task.json"
        if not task_path.exists():
            raise QualityTaskNotFoundError(f"Quality task not found: {task_id}")

        task = _read_json(task_path)
        historical_diffs = [DiffItem.model_validate(diff) for diff in task.get("diffs", []) if isinstance(diff, dict)]
        replay = DiffQualityProcessor().process(historical_diffs)
        quality_decisions = replay.to_debug_payload()

        retained_ids = {diff.diff_id for diff in replay.diffs}
        suppression_reasons = _suppression_reasons_by_diff_id(quality_decisions)
        decisions_by_diff_id = _decision_actions_by_diff_id(quality_decisions)
        retained_diffs = [_review_diff_payload(diff) for diff in replay.diffs]
        suppressed_diffs = [
            _review_diff_payload(
                diff,
                suppression_reason=suppression_reasons.get(diff.diff_id, ""),
                quality_decisions=decisions_by_diff_id.get(diff.diff_id, []),
            )
            for diff in historical_diffs
            if diff.diff_id not in retained_ids
        ]

        return {
            "task_id": task.get("task_id") or task_id,
            "status": task.get("status", ""),
            "original_filename": task.get("original_filename", ""),
            "compare_filename": task.get("compare_filename", ""),
            "historical_diff_count": len(historical_diffs),
            "retained_diff_count": len(retained_diffs),
            "suppressed_diff_count": len(suppressed_diffs),
            "ocr_quality_summary": task.get("ocr_quality_summary") or {},
            "retained_diffs": retained_diffs,
            "suppressed_diffs": suppressed_diffs,
            "quality_decisions": quality_decisions,
            "debug_artifacts": _debug_artifact_summary(task_dir),
        }

    def update_expected_diff(
        self,
        case_id: str,
        index: int,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        case_dir, expected = self._read_expected_case(case_id)
        expected_diffs = _expected_diffs_list(expected)
        if index < 0 or index >= len(expected_diffs):
            raise QualityExpectedDiffNotFoundError(f"Expected diff not found: {case_id}[{index}]")

        current = expected_diffs[index]
        if not isinstance(current, dict):
            current = {}
        expected_diffs[index] = _allowed_expected_diff(current) | _allowed_expected_diff(patch)
        _write_expected_json(case_dir / "expected.json", expected)
        return self.get_case(case_id)

    def create_expected_diff(
        self,
        case_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        case_dir, expected = self._read_expected_case(case_id)
        expected_diffs = expected.setdefault("expected_diffs", [])
        if not isinstance(expected_diffs, list):
            expected_diffs = []
            expected["expected_diffs"] = expected_diffs

        next_diff = _allowed_expected_diff(payload)
        if _is_negative_expected_diff(next_diff) and _has_same_negative_expected_diff(
            expected_diffs,
            next_diff["source_actual_diff_id"],
        ):
            return self.get_case(case_id)

        expected_diffs.append(next_diff)
        _write_expected_json(case_dir / "expected.json", expected)
        return self.get_case(case_id)

    def delete_expected_diff(self, case_id: str, index: int) -> dict[str, Any]:
        case_dir, expected = self._read_expected_case(case_id)
        expected_diffs = _expected_diffs_list(expected)
        if index < 0 or index >= len(expected_diffs):
            raise QualityExpectedDiffNotFoundError(f"Expected diff not found: {case_id}[{index}]")

        del expected_diffs[index]
        _write_expected_json(case_dir / "expected.json", expected)
        return self.get_case(case_id)

    def evaluate_cases(
        self,
        dataset_splits: set[str] | None = None,
        run_id: str = "local-eval",
    ) -> dict[str, Any]:
        _safe_child_dir(self.output_root, run_id, "quality run")
        report = evaluate_case_root(self.case_root, dataset_splits=dataset_splits)
        return {
            "run_id": run_id,
            "status": "COMPLETED",
            "report": report,
        }

    def run_regression(
        self,
        dataset_splits: set[str] | None = None,
        baseline_name: str = "current",
        run_id: str = "local-regression",
    ) -> dict[str, Any]:
        output_dir = _safe_child_dir(self.output_root, run_id, "quality run")
        baseline_path = None
        if baseline_name:
            candidate = _safe_child_file(
                self.output_root / "baselines",
                baseline_name,
                ".json",
                "quality baseline",
            )
            if candidate.exists():
                baseline_path = candidate

        summary = run_regression(
            case_root=self.case_root,
            output_dir=output_dir,
            baseline_path=baseline_path,
            thresholds_path=None,
            run_id=run_id,
            html_output=None,
            write_baseline=None,
            dataset_splits=dataset_splits,
        )
        return {
            "run_id": summary["run_id"],
            "status": summary["status"],
            "report": _read_json(output_dir / "quality.json"),
            "comparison": _read_json(output_dir / "baseline_comparison.json"),
        }

    def _case_dir(self, case_id: str) -> Path:
        return _safe_child_dir(self.case_root, case_id, "quality case")

    def _task_dir(self, task_id: str) -> Path:
        return _safe_child_dir(self.task_root, task_id, "quality task")

    def _read_expected_case(self, case_id: str) -> tuple[Path, dict[str, Any]]:
        case_dir = self._case_dir(case_id)
        expected_path = case_dir / "expected.json"
        if not expected_path.exists():
            raise QualityCaseNotFoundError(f"Quality case not found: {case_id}")
        return case_dir, _read_expected_json(expected_path)

    def _build_summary(
        self,
        case_dir: Path,
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> dict[str, Any]:
        counts = _count_expected_statuses(expected.get("expected_diffs", []))
        source_files = expected.get("source_files") or {}

        return {
            "case_id": str(expected.get("case_id") or case_dir.name),
            "schema_version": str(expected.get("schema_version") or "1.0"),
            "dataset_split": str(expected.get("dataset_split") or "legacy"),
            "case_tags": list(expected["case_tags"] if "case_tags" in expected else expected.get("tags") or []),
            "baseline_required": bool(expected.get("baseline_required", False)),
            "source_task_id": expected.get("source_task_id", ""),
            "original_filename": source_files.get("original_filename", ""),
            "compare_filename": source_files.get("compare_filename", ""),
            "approved_expected_count": counts["approved"],
            "draft_expected_count": counts["draft"],
            "rejected_expected_count": counts["rejected"],
            "actual_diff_count": len(actual.get("diffs", [])),
            "has_actual_json": (case_dir / "actual.json").exists(),
            "has_source_pdfs": _has_source_pdfs(case_dir),
        }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path.name}")
    return payload


def _read_expected_json(path: Path) -> dict[str, Any]:
    try:
        payload = _read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise QualityCaseInvalidError(f"Invalid quality case expected.json: {path.parent.name}") from exc
    if not isinstance(payload.get("expected_diffs", []), list):
        raise QualityCaseInvalidError(f"Invalid quality case expected.json: {path.parent.name}")
    return payload


def _write_expected_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _resolve_path(path: Path) -> Path:
    return path.expanduser().resolve()


def ensure_quality_paths_separated(
    target_root: Path,
    seed_root: Path,
) -> tuple[Path, Path]:
    target = _resolve_path(target_root)
    seed = _resolve_path(seed_root)
    if target == seed or target in seed.parents or seed in target.parents:
        raise QualityCasesPathConflictError(
            "QUALITY_CASES_PATH_CONFLICT: quality cases target and seed directories "
            "must be separate and must not contain one another"
        )
    return target, seed


def initialize_quality_cases(target_root: Path, seed_root: Path) -> dict[str, Any]:
    target, seed = ensure_quality_paths_separated(target_root, seed_root)
    manifest, seed_cases = _validate_seed_repository(seed)
    target.mkdir(parents=True, exist_ok=True)

    created_case_ids: list[str] = []
    preserved_case_ids: list[str] = []
    for case_id, source_case in seed_cases:
        target_case = target / case_id
        if target_case.exists():
            preserved_case_ids.append(case_id)
            continue

        staging_root = Path(
            tempfile.mkdtemp(
                prefix=f".{case_id}.",
                suffix=".staging",
                dir=target,
            )
        )
        staging_case = staging_root / case_id
        try:
            shutil.copytree(source_case, staging_case)
            _validate_seed_case(staging_case, case_id)
            _fsync_tree(staging_case)
            try:
                publish_directory_without_overwrite(staging_case, target_case)
            except FileExistsError:
                if not target_case.exists():
                    raise
                preserved_case_ids.append(case_id)
            else:
                _fsync_directory(target)
                created_case_ids.append(case_id)
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    atomic_write_json(
        target / "seed-manifest.json",
        {
            "schema_version": "1.0",
            "seed_version": manifest["seed_version"],
            "case_ids": [case_id for case_id, _ in seed_cases],
        },
    )
    return {
        "seed_version": manifest["seed_version"],
        "created_case_ids": created_case_ids,
        "preserved_case_ids": preserved_case_ids,
    }


def _validate_seed_repository(
    seed_root: Path,
) -> tuple[dict[str, Any], list[tuple[str, Path]]]:
    manifest_path = seed_root / "manifest.json"
    try:
        manifest = _read_json(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise QualityCaseInvalidError("Invalid quality seed manifest.json") from exc

    seed_version = manifest.get("seed_version")
    cases = manifest.get("cases")
    if manifest.get("schema_version") != "1.0":
        raise QualityCaseInvalidError("Invalid quality seed manifest schema_version")
    if not isinstance(seed_version, str) or not seed_version.strip():
        raise QualityCaseInvalidError("Invalid quality seed manifest seed_version")
    if not isinstance(cases, list) or not cases:
        raise QualityCaseInvalidError("Invalid quality seed manifest cases")

    validated: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for item in cases:
        if not isinstance(item, dict) or not isinstance(item.get("case_id"), str):
            raise QualityCaseInvalidError("Invalid quality seed manifest case entry")
        case_id = item["case_id"]
        if case_id in seen:
            raise QualityCaseInvalidError(f"Duplicate quality seed case: {case_id}")
        try:
            case_dir = _safe_child_dir(seed_root, case_id, "quality seed case")
        except InvalidQualityWorkbenchIdError as exc:
            raise QualityCaseInvalidError(f"Invalid quality seed manifest case id: {case_id}") from exc
        _validate_seed_case(case_dir, case_id)
        validated.append((case_id, case_dir))
        seen.add(case_id)
    return manifest, validated


def _validate_seed_case(case_dir: Path, case_id: str) -> None:
    if not case_dir.is_dir():
        raise QualityCaseInvalidError(f"Quality seed case is missing: {case_id}")
    expected = _read_expected_json(case_dir / "expected.json")
    if expected.get("case_id") != case_id:
        raise QualityCaseInvalidError(f"Quality seed expected.json case_id mismatch: {case_id}")
    if not (case_dir / "tests").is_dir():
        raise QualityCaseInvalidError(f"Quality seed case is missing required tests directory: {case_id}")
    actual_json = case_dir / "actual.json"
    has_pdfs = (case_dir / "original.pdf").is_file() and (case_dir / "compare.pdf").is_file()
    if actual_json.is_file():
        try:
            _read_json(actual_json)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise QualityCaseInvalidError(f"Invalid quality seed actual.json: {case_id}") from exc
    elif not has_pdfs:
        raise QualityCaseInvalidError(f"Quality seed case has no evaluation source: {case_id}")


def _fsync_tree(root: Path) -> None:
    directories = [root]
    for path in sorted(root.rglob("*")):
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        elif path.is_dir():
            directories.append(path)
    for directory in reversed(directories):
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_directory_without_overwrite(source: Path, target: Path) -> None:
    """Atomically publish a directory while preserving any existing target."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        rename_exclusive = libc.renamex_np
        rename_exclusive.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(os.fsencode(source), os.fsencode(target), 0x00000004)
        _raise_rename_error(result, target)
        return

    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename_no_replace = libc.renameat2
        rename_no_replace.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_no_replace.restype = ctypes.c_int
        result = rename_no_replace(
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(target),
            0x00000001,
        )
        _raise_rename_error(result, target)
        return

    raise NotImplementedError(f"Atomic no-overwrite directory publication is unsupported on {sys.platform}")


def _raise_rename_error(result: int, target: Path) -> None:
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number), target)
    raise OSError(error_number, os.strerror(error_number), target)


def _safe_child_dir(root: Path, identifier: str, label: str) -> Path:
    if not re.fullmatch(SAFE_ID_RE, identifier) or identifier in {".", ".."}:
        raise InvalidQualityWorkbenchIdError(f"Invalid {label} id: {identifier}")
    root_path = root.resolve()
    child_path = (root / identifier).resolve()
    if child_path.parent != root_path:
        raise InvalidQualityWorkbenchIdError(f"Invalid {label} id: {identifier}")
    return child_path


def _safe_child_file(root: Path, identifier: str, suffix: str, label: str) -> Path:
    if not re.fullmatch(SAFE_ID_RE, identifier) or identifier in {".", ".."}:
        raise InvalidQualityWorkbenchIdError(f"Invalid {label} id: {identifier}")
    root_path = root.resolve()
    child_path = (root / f"{identifier}{suffix}").resolve()
    if child_path.parent != root_path:
        raise InvalidQualityWorkbenchIdError(f"Invalid {label} id: {identifier}")
    return child_path


def _expected_diffs_list(expected: dict[str, Any]) -> list[Any]:
    expected_diffs = expected.get("expected_diffs")
    if isinstance(expected_diffs, list):
        return expected_diffs
    return []


def _allowed_expected_diff(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key in EXPECTED_DIFF_ALLOWED_FIELDS}


def _is_negative_expected_diff(diff: dict[str, Any]) -> bool:
    source_actual_diff_id = diff.get("source_actual_diff_id")
    return (
        diff.get("review_status") == "REJECTED"
        and diff.get("should_not_match_again") is True
        and isinstance(source_actual_diff_id, str)
        and len(source_actual_diff_id) > 0
    )


def _has_same_negative_expected_diff(
    expected_diffs: list[Any],
    source_actual_diff_id: str,
) -> bool:
    for diff in expected_diffs:
        if not isinstance(diff, dict):
            continue
        if (
            diff.get("review_status") == "REJECTED"
            and diff.get("should_not_match_again") is True
            and diff.get("source_actual_diff_id") == source_actual_diff_id
        ):
            return True
    return False


def _count_expected_statuses(expected_diffs: Any) -> dict[str, int]:
    counts = {"approved": 0, "draft": 0, "rejected": 0}
    if not isinstance(expected_diffs, list):
        return counts

    for diff in expected_diffs:
        if not isinstance(diff, dict):
            continue
        status = str(diff.get("review_status", "")).upper()
        if status in ("", "APPROVED"):
            counts["approved"] += 1
        elif status == "REJECTED":
            counts["rejected"] += 1
        else:
            counts["draft"] += 1
    return counts


def _summarize_actual_diff(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "diff_id": diff.get("diff_id", ""),
        "diff_type": diff.get("diff_type", ""),
        "source_type": diff.get("source_type", ""),
        "title": diff.get("title", ""),
        "quality_status": diff.get("quality_status", ""),
        "review_flags": diff.get("review_flags", []),
    }


def _review_diff_payload(
    diff: DiffItem,
    *,
    suppression_reason: str | None = None,
    quality_decisions: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "diff_id": diff.diff_id,
        "diff_type": diff.diff_type,
        "source_type": diff.source_type,
        "title": diff.title,
        "quality_status": diff.quality_status,
        "review_flags": list(diff.review_flags),
        "match_score": diff.match_score,
        "original_snippet": diff.original_snippet,
        "compare_snippet": diff.compare_snippet,
    }
    if suppression_reason is not None:
        payload["suppression_reason"] = suppression_reason
    if quality_decisions is not None:
        payload["quality_decisions"] = quality_decisions
    return payload


def _suppression_reasons_by_diff_id(
    quality_decisions: list[dict[str, Any]],
) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for decision in quality_decisions:
        action = decision.get("action")
        detail = decision.get("detail")
        if not isinstance(detail, dict):
            continue
        if action == "cross_source_merged":
            merged_diff_id = detail.get("merged_diff_id")
            if isinstance(merged_diff_id, str) and merged_diff_id:
                reasons[merged_diff_id] = "cross_source_merged"
            continue
        if action != "suppressed_low_value_noise":
            continue
        reason = detail.get("reason")
        if isinstance(reason, str):
            reasons[str(decision.get("diff_id", ""))] = reason
    return reasons


def _decision_actions_by_diff_id(
    quality_decisions: list[dict[str, Any]],
) -> dict[str, list[str]]:
    actions: dict[str, list[str]] = {}
    for decision in quality_decisions:
        diff_id = str(decision.get("diff_id", ""))
        action = decision.get("action")
        if diff_id and isinstance(action, str):
            actions.setdefault(diff_id, []).append(action)
        detail = decision.get("detail")
        if action == "cross_source_merged" and isinstance(detail, dict):
            merged_diff_id = detail.get("merged_diff_id")
            if isinstance(merged_diff_id, str) and merged_diff_id:
                actions.setdefault(merged_diff_id, []).append(action)
    return actions


def _debug_artifact_summary(task_dir: Path) -> dict[str, bool]:
    debug_dir = task_dir / "debug"
    return {
        "has_diff_quality": (debug_dir / "diff_quality.json").exists(),
        "has_diff_decisions": (debug_dir / "diff_decisions.json").exists(),
        "has_ocr_quality": (debug_dir / "ocr_quality.json").exists(),
        "has_clause_matches": (debug_dir / "clause_matches.json").exists(),
    }


def _has_source_pdfs(case_dir: Path) -> bool:
    return (case_dir / "original.pdf").exists() and (case_dir / "compare.pdf").exists()
