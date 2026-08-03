from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.errors import DocumentProcessingError
from app.models import Document, DocumentProfile, LayoutQualityReport


class DocumentExtractionError(DocumentProcessingError):
    pass


@dataclass
class ExtractionResult:
    document: Document
    extractor_used: str
    raw_result_path: str = ""
    warnings: list[str] = field(default_factory=list)
    profile: DocumentProfile | None = None
    layout_quality: LayoutQualityReport | None = None
    performance: dict[str, Any] = field(default_factory=dict)


class DocumentExtractor(Protocol):
    name: str

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        raise NotImplementedError
