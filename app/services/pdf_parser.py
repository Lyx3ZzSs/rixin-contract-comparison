from __future__ import annotations

from pathlib import Path

from app.models import Document
from app.services.extractors.base import DocumentExtractionError
from app.services.extractors.pymupdf import PyMuPDFExtractor


class PdfParseError(DocumentExtractionError):
    pass


class PdfParser:
    def parse(self, path: str | Path) -> Document:
        try:
            return PyMuPDFExtractor().extract(path).document
        except DocumentExtractionError as exc:
            raise PdfParseError(str(exc)) from exc
