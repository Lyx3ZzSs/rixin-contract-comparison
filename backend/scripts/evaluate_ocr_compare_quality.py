from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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


def _has_actual_source(case_dir: Path) -> bool:
    return (case_dir / "actual.json").exists() or (
        (case_dir / "original.pdf").exists() and (case_dir / "compare.pdf").exists()
    )
