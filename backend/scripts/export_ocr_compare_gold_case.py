from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def export_gold_case(
    task_dir: Path,
    output_dir: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    task = _read_json(task_dir / "task.json")
    _validate_task(task)

    output_dir.mkdir(parents=True, exist_ok=True)
    expected_path = output_dir / "expected.json"
    if expected_path.exists() and _has_protected_expected(expected_path) and not force:
        raise FileExistsError(
            f"{expected_path} contains reviewed expected.json entries; "
            "pass --force to overwrite"
        )

    expected = _build_expected_payload(output_dir.name, task)
    _write_json(output_dir / "actual.json", task)
    _write_json(expected_path, expected)
    (output_dir / "README.md").write_text(
        _readme(output_dir.name, task),
        encoding="utf-8",
    )

    return {
        "case_id": output_dir.name,
        "task_id": str(task["task_id"]),
        "expected_diff_count": len(expected["expected_diffs"]),
        "actual_diff_count": len(task.get("diffs", [])),
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _validate_task(task: dict[str, Any]) -> None:
    if not task.get("task_id"):
        raise ValueError("task.json is missing task_id")
    if not isinstance(task.get("diffs"), list):
        raise ValueError("task.json is missing diffs list")


def _has_protected_expected(path: Path) -> bool:
    payload = _read_json(path)
    return any(
        item.get("review_status") != "DRAFT"
        for item in payload.get("expected_diffs", [])
    )


def _build_expected_payload(case_id: str, task: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "tags": ["exported", "requires_human_review"],
        "source_task_id": task["task_id"],
        "source_files": {
            "original_filename": task.get("original_filename", ""),
            "compare_filename": task.get("compare_filename", ""),
        },
        "annotation_status": "DRAFT",
        "expected_diffs": [_draft_expected_diff(diff) for diff in task.get("diffs", [])],
        "quality_expectations": {
            "max_task_failures": 0,
            "min_recall": 1.0,
            "max_false_positive_count": 0,
            "min_evidence_hit_rate": 1.0,
        },
    }


def _draft_expected_diff(diff: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "diff_type": diff.get("diff_type", ""),
        "source_type": diff.get("source_type", ""),
        "title_contains": _snippet(diff.get("title", "")),
        "source_actual_diff_id": diff.get("diff_id", ""),
        "review_status": "DRAFT",
        "severity": _severity(diff),
        "notes": (
            "Generated draft, not reviewed gold. Change review_status to APPROVED "
            "after human validation."
        ),
    }
    original = _snippet(diff.get("original_text") or diff.get("original_snippet") or "")
    compare = _snippet(diff.get("compare_text") or diff.get("compare_snippet") or "")
    if original:
        payload["original_contains"] = original
    if compare:
        payload["compare_contains"] = compare
    return {key: value for key, value in payload.items() if value not in ("", [], None)}


def _snippet(value: Any, *, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _severity(diff: dict[str, Any]) -> str:
    flags = set(diff.get("review_flags", []))
    source_type = str(diff.get("source_type", ""))
    title = str(diff.get("title", ""))
    metadata_key_tokens = (
        "date",
        "amount",
        "price",
        "party",
        "contract",
        "number",
        "签订日期",
        "合同编号",
        "合同金额",
        "金额",
        "价款",
        "价格",
        "当事人",
        "甲方",
        "乙方",
    )
    if "CRITICAL_VALUE_CHANGE" in flags or source_type == "seal":
        return "critical"
    if source_type == "metadata" and any(
        token in title.lower() for token in metadata_key_tokens
    ):
        return "critical"
    if diff.get("quality_status") == "NEEDS_REVIEW" or any("OCR" in flag for flag in flags):
        return "major"
    return "minor"


def _readme(case_id: str, task: dict[str, Any]) -> str:
    ocr_summary = task.get("ocr_quality_summary") or {}
    return "\n".join(
        [
            f"# OCR Compare Gold Case: {case_id}",
            "",
            "Generated draft, not reviewed gold.",
            "",
            f"- Source task: `{task.get('task_id', '')}`",
            f"- Original file: `{task.get('original_filename', '')}`",
            f"- Compare file: `{task.get('compare_filename', '')}`",
            f"- Task status: `{task.get('status', '')}`",
            f"- OCR status: `{ocr_summary.get('status', '')}`",
            f"- OCR risk pages: `{ocr_summary.get('risk_page_count', 0)}`",
            f"- OCR affected diffs: `{ocr_summary.get('affected_diff_count', 0)}`",
            "",
            "Review steps:",
            "1. Open `expected.json`.",
            "2. Remove generated entries that are not true contract differences.",
            "3. Add missing expected differences found by human review.",
            "4. Change validated entries from `DRAFT` to `APPROVED`.",
            "5. Treat `actual.json` and `expected.json` as sensitive because they may "
            "contain sensitive contract or business data.",
            "6. Keep sensitive PDFs out of Git unless explicitly approved.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a compare task as an OCR gold-case draft."
    )
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    summary = export_gold_case(args.task_dir, args.output_dir, force=args.force)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
