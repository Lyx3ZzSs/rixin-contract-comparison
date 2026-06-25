from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import CompareTask  # noqa: E402
from app.services.compare_service import CompareService  # noqa: E402


@dataclass(frozen=True)
class OcrCompareCase:
    case_id: str
    case_dir: Path


def discover_cases(case_root: Path) -> list[OcrCompareCase]:
    return [
        OcrCompareCase(case_id=path.name, case_dir=path)
        for path in sorted(case_root.iterdir())
        if path.is_dir() and (path / "expected.json").exists() and _has_actual_source(path)
    ]


def load_case_inputs(case: OcrCompareCase) -> tuple[dict[str, Any], CompareTask]:
    expected = _read_json(case.case_dir / "expected.json")
    return expected, _load_or_run_actual(case)


def _load_or_run_actual(case: OcrCompareCase) -> CompareTask:
    actual_json = case.case_dir / "actual.json"
    if actual_json.exists():
        return CompareTask(**_read_json(actual_json))

    original_pdf = case.case_dir / "original.pdf"
    compare_pdf = case.case_dir / "compare.pdf"
    if not original_pdf.exists() or not compare_pdf.exists():
        raise FileNotFoundError(
            f"{case.case_dir} must contain actual.json or original.pdf plus compare.pdf"
        )
    return CompareService().compare(
        original_pdf,
        compare_pdf,
        task_id=f"EVAL_OCR_{case.case_id.upper()}",
        original_filename=original_pdf.name,
        compare_filename=compare_pdf.name,
    )


def _has_actual_source(case_dir: Path) -> bool:
    return (case_dir / "actual.json").exists() or (
        (case_dir / "original.pdf").exists() and (case_dir / "compare.pdf").exists()
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
