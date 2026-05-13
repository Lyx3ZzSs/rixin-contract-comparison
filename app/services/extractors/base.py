from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.models import Document


class DocumentExtractionError(ValueError):
    pass


@dataclass
class ExtractionResult:
    document: Document
    extractor_used: str
    raw_result_path: str = ""
    warnings: list[str] = field(default_factory=list)


class DocumentExtractor(Protocol):
    name: str

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        raise NotImplementedError
