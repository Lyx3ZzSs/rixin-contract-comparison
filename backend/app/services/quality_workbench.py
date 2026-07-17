from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import secrets
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.infrastructure.atomic_files import atomic_write_json, atomic_write_text
from app.models import CompareTask, DiffItem
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
EXPECTED_DIFF_TYPES = {"ADD", "DELETE", "MODIFY"}
EXPECTED_SOURCE_TYPES = {
    "clause",
    "header_footer",
    "table",
    "metadata",
    "seal",
    "page",
    "signing_region",
}
EXPECTED_REVIEW_STATUSES = {"", "APPROVED", "DRAFT", "REJECTED"}
EXPECTED_STRING_FIELDS = EXPECTED_DIFF_ALLOWED_FIELDS - {
    "should_not_match_again",
    "expected_evidence",
}


class QualityWorkbenchError(Exception):
    """Base error for quality workbench operations."""


class InvalidQualityWorkbenchIdError(QualityWorkbenchError):
    """Raised when a case id is not safe to use as a path segment."""

    error_code = "QUALITY_PATH_INVALID"


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


class _VerifiedDirectory:
    """Keep writes anchored to the directory that passed the path guard.

    A resolved path check alone is vulnerable if the directory is renamed and
    replaced with a symlink between the check and a later write.  The open file
    descriptor below is the authority for staging paths; a fresh no-follow open
    before each externally visible write also makes the operation fail closed
    when its configured directory has changed identity.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor = _open_directory_no_follow(path)
        self.identity = _directory_identity(self.descriptor)

    def close(self) -> None:
        os.close(self.descriptor)

    def assert_stable(self) -> None:
        descriptor = _open_directory_no_follow(self.path)
        try:
            if _directory_identity(descriptor) != self.identity:
                raise QualityCasesPathConflictError(
                    "QUALITY_CASES_PATH_CONFLICT: quality case directory changed during operation"
                )
        finally:
            os.close(descriptor)

    def child_path(self, name: str) -> Path:
        if sys.platform.startswith("linux"):
            return _directory_fd_path(self.descriptor) / name
        # Docker production uses the descriptor-backed Linux path above.  macOS
        # does not permit directory traversal through /dev/fd, so retain the
        # identity checks around each write on the local development platform.
        return self.path / name

    def entry_exists(self, name: str) -> bool:
        try:
            os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    def make_staging_directory(self, case_id: str) -> Path:
        for _ in range(100):
            name = f".{case_id}.{secrets.token_hex(8)}.staging"
            try:
                os.mkdir(name, mode=0o700, dir_fd=self.descriptor)
            except FileExistsError:
                continue
            return self.child_path(name)
        raise FileExistsError("Unable to allocate quality case staging directory")


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
        operation_case_root = _resolve_path(self.case_root)
        operation_seed_root = (
            _resolve_path(self.seed_root) if self.seed_root is not None else None
        )
        if self.seed_root is not None:
            operation_case_root, operation_seed_root = (
                ensure_quality_paths_separated(self.case_root, self.seed_root)
            )
        # Validate case_id before looking up the source task.  This retains the
        # stable path-invalid contract even when task_id does not exist.
        self._case_dir(case_id)
        task_dir = self._task_dir(task_id)
        if not (task_dir / "task.json").exists():
            raise QualityTaskNotFoundError(f"Quality task not found: {task_id}")

        if operation_seed_root is not None:
            operation_case_root, operation_seed_root = _ensure_quality_paths_stable(
                self.case_root,
                self.seed_root,
                operation_case_root,
                operation_seed_root,
            )
        operation_case_root.mkdir(parents=True, exist_ok=True)
        target_directory = _VerifiedDirectory(operation_case_root)
        seed_directory = (
            _VerifiedDirectory(operation_seed_root)
            if operation_seed_root is not None
            else None
        )
        try:
            _assert_quality_operation_stable(
                target_directory,
                seed_directory,
                self.case_root,
                self.seed_root,
                operation_case_root,
                operation_seed_root,
            )
            if target_directory.entry_exists(case_id):
                raise FileExistsError(f"Quality case already exists: {case_id}")
            staging_root = target_directory.make_staging_directory(case_id)
            staging_case = staging_root / case_id
            summary = export_gold_case(task_dir, staging_case, force=False)
            _read_expected_json(staging_case / "expected.json")
            _read_actual_task(staging_case / "actual.json", case_id)
            _fsync_tree(staging_case)
            _assert_quality_operation_stable(
                target_directory,
                seed_directory,
                self.case_root,
                self.seed_root,
                operation_case_root,
                operation_seed_root,
            )
            case_dir = target_directory.child_path(case_id)
            publish_directory_without_overwrite(staging_case, case_dir)
            _fsync_descriptor(target_directory.descriptor)
            _assert_quality_operation_stable(
                target_directory,
                seed_directory,
                self.case_root,
                self.seed_root,
                operation_case_root,
                operation_seed_root,
            )
            return summary
        finally:
            if "staging_root" in locals():
                shutil.rmtree(staging_root, ignore_errors=True)
            if seed_directory is not None:
                seed_directory.close()
            target_directory.close()

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
        _validate_expected_candidate(expected, case_id)
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
        _validate_expected_candidate(expected, case_id)
        _write_expected_json(case_dir / "expected.json", expected)
        return self.get_case(case_id)

    def delete_expected_diff(self, case_id: str, index: int) -> dict[str, Any]:
        case_dir, expected = self._read_expected_case(case_id)
        expected_diffs = _expected_diffs_list(expected)
        if index < 0 or index >= len(expected_diffs):
            raise QualityExpectedDiffNotFoundError(f"Expected diff not found: {case_id}[{index}]")

        del expected_diffs[index]
        _validate_expected_candidate(expected, case_id)
        _write_expected_json(case_dir / "expected.json", expected)
        return self.get_case(case_id)

    def evaluate_cases(
        self,
        dataset_splits: set[str] | None = None,
        run_id: str = "local-eval",
    ) -> dict[str, Any]:
        _safe_child_dir(self.output_root, run_id, "quality run")
        self._preflight_evaluation_cases()
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

        self._preflight_evaluation_cases()

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

    def _preflight_evaluation_cases(self) -> None:
        if not self.case_root.exists():
            return
        for case_dir in sorted(self.case_root.iterdir()):
            if not case_dir.is_dir() or not (case_dir / "expected.json").exists():
                continue
            _validate_evaluation_case(case_dir, case_dir.name)

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
    try:
        _validate_expected_payload(payload)
    except (TypeError, ValueError):
        raise QualityCaseInvalidError(f"Invalid quality case expected.json: {path.parent.name}")
    return payload


def _validate_expected_candidate(payload: dict[str, Any], case_id: str) -> None:
    try:
        _validate_expected_payload(payload)
    except (TypeError, ValueError) as exc:
        raise QualityCaseInvalidError(
            f"Invalid quality case expected.json: {case_id}"
        ) from exc


def _validate_expected_payload(payload: dict[str, Any]) -> None:
    if "expected_diffs" not in payload or not isinstance(
        payload["expected_diffs"], list
    ):
        raise ValueError("expected_diffs must be a list")
    if "case_id" in payload and not isinstance(payload["case_id"], str):
        raise ValueError("case_id must be a string")
    if "dataset_split" in payload and not isinstance(payload["dataset_split"], str):
        raise ValueError("dataset_split must be a string")
    for field in ("case_tags", "tags"):
        if field in payload and (
            not isinstance(payload[field], list)
            or not all(isinstance(item, str) for item in payload[field])
        ):
            raise ValueError(f"{field} must be a string list")
    if "baseline_required" in payload and not isinstance(
        payload["baseline_required"], bool
    ):
        raise ValueError("baseline_required must be a boolean")
    if "source_files" in payload and not isinstance(payload["source_files"], dict):
        raise ValueError("source_files must be an object")

    for index, item in enumerate(payload["expected_diffs"]):
        if not isinstance(item, dict):
            raise ValueError(f"expected_diffs[{index}] must be an object")
        if item.get("diff_type") not in (None, *EXPECTED_DIFF_TYPES):
            raise ValueError(f"expected_diffs[{index}].diff_type is invalid")
        if item.get("source_type") not in (None, "", *EXPECTED_SOURCE_TYPES):
            raise ValueError(f"expected_diffs[{index}].source_type is invalid")
        if item.get("review_status") not in (None, *EXPECTED_REVIEW_STATUSES):
            raise ValueError(f"expected_diffs[{index}].review_status is invalid")
        for field in EXPECTED_STRING_FIELDS:
            if field in item and not isinstance(item[field], str):
                raise ValueError(f"expected_diffs[{index}].{field} must be a string")
        if "should_not_match_again" in item and not isinstance(
            item["should_not_match_again"], bool
        ):
            raise ValueError(
                f"expected_diffs[{index}].should_not_match_again must be a boolean"
            )
        if "expected_evidence" in item and (
            not isinstance(item["expected_evidence"], list)
            or not all(
                isinstance(evidence, dict) for evidence in item["expected_evidence"]
            )
        ):
            raise ValueError(
                f"expected_diffs[{index}].expected_evidence must be an object list"
            )
        # The evaluator deliberately avoids matching only on type/source: that
        # would turn every same-kind diff into a false positive.  A seed entry
        # must therefore carry at least one usable textual signal, or complete
        # positional evidence that the evaluator can compare on both sides.
        has_text_signal = any(
            isinstance(item.get(field), str) and bool(item[field].strip())
            for field in ("title_contains", "original_contains", "compare_contains")
        )
        has_evidence_signal = bool(item.get("expected_evidence")) and all(
            _is_evaluation_evidence(evidence)
            for evidence in item.get("expected_evidence", [])
        )
        if not has_text_signal and not has_evidence_signal:
            raise ValueError(
                f"expected_diffs[{index}] requires a usable matching signal"
            )


def _is_evaluation_evidence(evidence: dict[str, Any]) -> bool:
    if evidence.get("side") not in {"original", "compare"}:
        return False
    if not isinstance(evidence.get("page_no"), int) or evidence["page_no"] < 1:
        return False
    bbox = evidence.get("bbox")
    if not isinstance(bbox, dict):
        return False
    return all(
        isinstance(bbox.get(field), (int, float)) and not isinstance(bbox[field], bool)
        for field in ("x0", "y0", "x1", "y1")
    )


def _read_actual_task(path: Path, case_id: str) -> dict[str, Any]:
    try:
        payload = _read_json(path)
        CompareTask.model_validate(payload)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        ValidationError,
    ) as exc:
        raise QualityCaseInvalidError(
            f"Invalid quality case actual.json: {case_id}"
        ) from exc
    return payload


def _validate_evaluation_case(case_dir: Path, case_id: str) -> None:
    expected = _read_expected_json(case_dir / "expected.json")
    if expected.get("case_id") not in (None, case_id):
        raise QualityCaseInvalidError(
            f"Quality case expected.json case_id mismatch: {case_id}"
        )
    actual_json = case_dir / "actual.json"
    if actual_json.is_file():
        _read_actual_task(actual_json, case_id)
        return
    if not (
        (case_dir / "original.pdf").is_file()
        and (case_dir / "compare.pdf").is_file()
    ):
        raise QualityCaseInvalidError(
            f"Quality case has no evaluation source: {case_id}"
        )


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


def _ensure_quality_paths_stable(
    target_reference: Path,
    seed_reference: Path,
    expected_target: Path,
    expected_seed: Path,
) -> tuple[Path, Path]:
    target, seed = ensure_quality_paths_separated(target_reference, seed_reference)
    if target != expected_target or seed != expected_seed:
        raise QualityCasesPathConflictError(
            "QUALITY_CASES_PATH_CONFLICT: quality case paths changed during operation"
        )
    return target, seed


def _open_directory_no_follow(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise QualityCasesPathConflictError(
                "QUALITY_CASES_PATH_CONFLICT: quality case directory became a symlink or non-directory"
            ) from exc
        raise
    try:
        path_stat = os.lstat(path)
        descriptor_stat = os.fstat(descriptor)
        if (
            stat.S_ISLNK(path_stat.st_mode)
            or not stat.S_ISDIR(descriptor_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise QualityCasesPathConflictError(
                "QUALITY_CASES_PATH_CONFLICT: quality case directory identity changed"
            )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _directory_identity(descriptor: int) -> tuple[int, int]:
    directory_stat = os.fstat(descriptor)
    return directory_stat.st_dev, directory_stat.st_ino


def _directory_fd_path(descriptor: int) -> Path:
    # Both supported deployment platforms expose an open directory through this
    # descriptor-backed path.  It keeps shutil/atomic helpers on the original
    # directory even if the configured pathname is replaced concurrently.
    return Path(f"/dev/fd/{descriptor}")


def _fsync_descriptor(descriptor: int) -> None:
    os.fsync(descriptor)


def _assert_quality_operation_stable(
    target_directory: _VerifiedDirectory,
    seed_directory: _VerifiedDirectory | None,
    target_reference: Path,
    seed_reference: Path | None,
    expected_target: Path,
    expected_seed: Path | None,
) -> None:
    target_directory.assert_stable()
    if seed_directory is not None:
        seed_directory.assert_stable()
        if seed_reference is None or expected_seed is None:
            raise AssertionError("Seed reference must accompany seed directory")
        _ensure_quality_paths_stable(
            target_reference,
            seed_reference,
            expected_target,
            expected_seed,
        )


def initialize_quality_cases(target_root: Path, seed_root: Path) -> dict[str, Any]:
    target, seed = ensure_quality_paths_separated(target_root, seed_root)
    manifest, seed_cases = _validate_seed_repository(seed)
    target, seed = _ensure_quality_paths_stable(
        target_root,
        seed_root,
        target,
        seed,
    )
    target.mkdir(parents=True, exist_ok=True)
    target_directory = _VerifiedDirectory(target)
    seed_directory = _VerifiedDirectory(seed)
    try:
        created_case_ids: list[str] = []
        preserved_case_ids: list[str] = []
        for case_id, _source_case in seed_cases:
            _assert_quality_operation_stable(
                target_directory,
                seed_directory,
                target_root,
                seed_root,
                target,
                seed,
            )
            if target_directory.entry_exists(case_id):
                preserved_case_ids.append(case_id)
                continue

            staging_root = target_directory.make_staging_directory(case_id)
            staging_case = staging_root / case_id
            try:
                shutil.copytree(seed_directory.child_path(case_id), staging_case)
                _validate_seed_case(staging_case, case_id)
                _fsync_tree(staging_case)
                _assert_quality_operation_stable(
                    target_directory,
                    seed_directory,
                    target_root,
                    seed_root,
                    target,
                    seed,
                )
                target_case = target_directory.child_path(case_id)
                try:
                    publish_directory_without_overwrite(staging_case, target_case)
                except FileExistsError:
                    if not target_directory.entry_exists(case_id):
                        raise
                    preserved_case_ids.append(case_id)
                else:
                    _fsync_descriptor(target_directory.descriptor)
                    created_case_ids.append(case_id)
            finally:
                shutil.rmtree(staging_root, ignore_errors=True)

        _assert_quality_operation_stable(
            target_directory,
            seed_directory,
            target_root,
            seed_root,
            target,
            seed,
        )
        atomic_write_json(
            target_directory.child_path("seed-manifest.json"),
            {
                "schema_version": "1.0",
                "seed_version": manifest["seed_version"],
                "case_ids": [case_id for case_id, _ in seed_cases],
            },
        )
        _assert_quality_operation_stable(
            target_directory,
            seed_directory,
            target_root,
            seed_root,
            target,
            seed,
        )
        return {
            "seed_version": manifest["seed_version"],
            "created_case_ids": created_case_ids,
            "preserved_case_ids": preserved_case_ids,
        }
    finally:
        seed_directory.close()
        target_directory.close()


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
        _read_actual_task(actual_json, case_id)
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
