from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

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


class QualityWorkbenchService:
    def __init__(self, case_root: Path, task_root: Path, output_root: Path) -> None:
        self.case_root = case_root
        self.task_root = task_root
        self.output_root = output_root

    def list_cases(self) -> list[dict[str, Any]]:
        if not self.case_root.exists():
            return []

        cases = []
        for case_dir in sorted(self.case_root.iterdir()):
            if not case_dir.is_dir() or not (case_dir / "expected.json").exists():
                continue
            expected = _read_json(case_dir / "expected.json")
            actual = (
                _read_json(case_dir / "actual.json")
                if (case_dir / "actual.json").exists()
                else {}
            )
            cases.append(self._build_summary(case_dir, expected, actual))
        return cases

    def get_case(self, case_id: str) -> dict[str, Any]:
        case_dir = self._case_dir(case_id)
        expected_path = case_dir / "expected.json"
        if not expected_path.exists():
            raise QualityCaseNotFoundError(f"Quality case not found: {case_id}")

        expected = _read_json(expected_path)
        actual_path = case_dir / "actual.json"
        actual = _read_json(actual_path) if actual_path.exists() else {}
        readme_path = case_dir / "README.md"

        return {
            "summary": self._build_summary(case_dir, expected, actual),
            "readme": (
                readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
            ),
            "expected": expected,
            "actual_diffs": [
                _summarize_actual_diff(diff) for diff in actual.get("diffs", [])
            ],
        }

    def export_case(
        self,
        task_id: str,
        case_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        task_dir = self._task_dir(task_id)
        case_dir = self._case_dir(case_id)
        if not (task_dir / "task.json").exists():
            raise QualityTaskNotFoundError(f"Quality task not found: {task_id}")

        return export_gold_case(task_dir, case_dir, force=force)

    def review_task(self, task_id: str) -> dict[str, Any]:
        task_dir = self._task_dir(task_id)
        task_path = task_dir / "task.json"
        if not task_path.exists():
            raise QualityTaskNotFoundError(f"Quality task not found: {task_id}")

        task = _read_json(task_path)
        historical_diffs = [
            DiffItem.model_validate(diff)
            for diff in task.get("diffs", [])
            if isinstance(diff, dict)
        ]
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
            raise QualityExpectedDiffNotFoundError(
                f"Expected diff not found: {case_id}[{index}]"
            )

        current = expected_diffs[index]
        if not isinstance(current, dict):
            current = {}
        expected_diffs[index] = _allowed_expected_diff(current) | _allowed_expected_diff(
            patch
        )
        _write_json_atomic(case_dir / "expected.json", expected)
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

        expected_diffs.append(_allowed_expected_diff(payload))
        _write_json_atomic(case_dir / "expected.json", expected)
        return self.get_case(case_id)

    def delete_expected_diff(self, case_id: str, index: int) -> dict[str, Any]:
        case_dir, expected = self._read_expected_case(case_id)
        expected_diffs = _expected_diffs_list(expected)
        if index < 0 or index >= len(expected_diffs):
            raise QualityExpectedDiffNotFoundError(
                f"Expected diff not found: {case_id}[{index}]"
            )

        del expected_diffs[index]
        _write_json_atomic(case_dir / "expected.json", expected)
        return self.get_case(case_id)

    def evaluate_cases(
        self,
        dataset_splits: set[str] | None = None,
        run_id: str = "local-eval",
    ) -> dict[str, Any]:
        _safe_child_dir(self.output_root / "runs", run_id, "quality run")
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
        output_dir = _safe_child_dir(self.output_root / "runs", run_id, "quality run")
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
        return case_dir, _read_json(expected_path)

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
            "case_tags": list(
                expected["case_tags"]
                if "case_tags" in expected
                else expected.get("tags") or []
            ),
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
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_child_dir(root: Path, identifier: str, label: str) -> Path:
    if (
        not re.fullmatch(SAFE_ID_RE, identifier)
        or identifier in {".", ".."}
    ):
        raise InvalidQualityWorkbenchIdError(f"Invalid {label} id: {identifier}")
    root_path = root.resolve()
    child_path = (root / identifier).resolve()
    if child_path.parent != root_path:
        raise InvalidQualityWorkbenchIdError(f"Invalid {label} id: {identifier}")
    return child_path


def _safe_child_file(root: Path, identifier: str, suffix: str, label: str) -> Path:
    if (
        not re.fullmatch(SAFE_ID_RE, identifier)
        or identifier in {".", ".."}
    ):
        raise InvalidQualityWorkbenchIdError(f"Invalid {label} id: {identifier}")
    root_path = root.resolve()
    child_path = (root / f"{identifier}{suffix}").resolve()
    if child_path.parent != root_path:
        raise InvalidQualityWorkbenchIdError(f"Invalid {label} id: {identifier}")
    return child_path


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(json.dumps(payload, ensure_ascii=False, indent=2))
            tmp_file.write("\n")
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def _expected_diffs_list(expected: dict[str, Any]) -> list[Any]:
    expected_diffs = expected.get("expected_diffs")
    if isinstance(expected_diffs, list):
        return expected_diffs
    return []


def _allowed_expected_diff(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key in EXPECTED_DIFF_ALLOWED_FIELDS
    }


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
