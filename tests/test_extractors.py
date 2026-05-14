from __future__ import annotations

from pathlib import Path

import pytest

from app.models import BBox, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError
from app.services.extractors.base import ExtractionResult
from app.services.extractors.factory import AutoDocumentExtractor, build_document_extractor
from app.services.extractors.paddleocr import PaddleOCRExtractor
from app.services.extractors.pymupdf import PyMuPDFExtractor


def test_build_document_extractor_defaults_to_auto() -> None:
    extractor = build_document_extractor()

    assert isinstance(extractor, AutoDocumentExtractor)
    assert extractor.name == "auto"


def test_build_document_extractor_supports_pymupdf() -> None:
    extractor = build_document_extractor("pymupdf")

    assert isinstance(extractor, PyMuPDFExtractor)
    assert extractor.name == "pymupdf"


def test_build_document_extractor_supports_paddleocr() -> None:
    extractor = build_document_extractor("paddleocr")

    assert isinstance(extractor, PaddleOCRExtractor)
    assert extractor.name == "paddleocr"


def test_paddleocr_payload_to_document_maps_pruned_result(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    payload = {
        "downloaded": [
            {
                "result": {
                    "ocrResults": [
                        {
                            "prunedResult": {
                                "rec_texts": ["1. 付款条款", "买方应在30日内付款。"],
                                "rec_scores": [0.98, 0.96],
                                "dt_polys": [
                                    [[60, 100], [240, 100], [240, 124], [60, 124]],
                                    [[60, 130], [320, 130], [320, 154], [60, 154]],
                                ],
                            }
                        }
                    ]
                }
            }
        ]
    }

    document = PaddleOCRExtractor().payload_to_document(payload, pdf)

    block = document.pages[0].blocks[0]
    assert document.filename == "scan.pdf"
    assert block.text == "1. 付款条款"
    assert block.bbox.x0 == 60
    assert block.bbox.y0 == 100
    assert block.bbox.x1 == 240
    assert block.bbox.y1 == 124
    assert block.confidence == 0.98


def test_auto_document_extractor_falls_back_to_paddleocr() -> None:
    class FailingExtractor:
        def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
            raise DocumentExtractionError("no text")

    class FakePaddleOCRExtractor:
        def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
            return ExtractionResult(
                document=Document(
                    filename="scan.pdf",
                    path="scan.pdf",
                    page_count=1,
                    pages=[
                        Page(
                            page_no=1,
                            width=600,
                            height=800,
                            blocks=[
                                TextBlock(
                                    block_id="p1",
                                    page_no=1,
                                    text="扫描合同",
                                    bbox=BBox(x0=10, y0=10, x1=100, y1=30),
                                )
                            ],
                        )
                    ],
                ),
                extractor_used="paddleocr",
            )

    result = AutoDocumentExtractor(primary=FailingExtractor(), fallback=FakePaddleOCRExtractor()).extract("scan.pdf")

    assert result.extractor_used == "pymupdf_fallback_paddleocr"
    assert "PaddleOCR" in result.warnings[0]


def test_paddleocr_payload_requires_text(tmp_path: Path) -> None:
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    with pytest.raises(DocumentExtractionError):
        PaddleOCRExtractor().payload_to_document({"downloaded": []}, pdf)


def test_paddleocr_payload_maps_legacy_line_items(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    payload = {
        "downloaded": {
            "pages": [
                {
                    "prunedResult": [
                        {"text": "合同标题", "bbox": [0.1, 0.125, 0.5, 0.2], "score": 0.9}
                    ]
                }
            ]
        }
    }

    document = PaddleOCRExtractor().payload_to_document(payload, pdf)

    block = document.pages[0].blocks[0]
    assert block.text == "合同标题"
    assert round(block.bbox.x0, 1) == 59.5
    assert round(block.bbox.y0, 1) == 105.2
    assert round(block.bbox.x1, 1) == 297.5
    assert round(block.bbox.y1, 1) == 168.4


def test_build_document_extractor_rejects_unsupported_name() -> None:
    with pytest.raises(DocumentExtractionError):
        build_document_extractor("unknown")
