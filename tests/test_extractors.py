from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.config import settings
from app.models import BBox, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError
from app.services.extractors.base import ExtractionResult
from app.services.extractors.factory import AutoDocumentExtractor, build_document_extractor
from app.services.extractors.paddleocr import PaddleOCRExtractor
from app.services.extractors.pymupdf import PyMuPDFExtractor


def test_build_document_extractor_defaults_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "document_extractor", "auto")

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


def test_paddleocr_sdk_payload_maps_word_boxes(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page(width=200, height=100)
    doc.save(str(pdf))
    doc.close()
    payload = [
        {
            "input_path": str(pdf),
            "page_index": 0,
            "doc_preprocessor_res": {"output_img_shape": [200, 400, 3]},
            "rec_texts": ["合同A"],
            "rec_scores": [0.99],
            "rec_polys": [[[40, 20], [160, 20], [160, 40], [40, 40]]],
            "text_word": [["合", "同", "A"]],
            "text_word_region": [
                [
                    ((40, 20), (80, 20), (80, 40), (40, 40)),
                    ((80, 20), (120, 20), (120, 40), (80, 40)),
                    ((120, 20), (160, 20), (160, 40), (120, 40)),
                ]
            ],
        }
    ]

    document = PaddleOCRExtractor().payload_to_document(payload, pdf)

    block = document.pages[0].blocks[0]
    assert block.text == "合同A"
    assert block.bbox.x0 == 20
    assert block.bbox.y0 == 10
    assert block.bbox.x1 == 80
    assert block.bbox.y1 == 20
    assert [char_box.char for char_box in block.char_boxes] == ["合", "同", "A"]
    assert [char_box.text_index for char_box in block.char_boxes] == [0, 1, 2]
    assert block.char_boxes[0].bbox.x0 == 20
    assert block.char_boxes[1].bbox.x0 == 40


def test_paddleocr_extract_uses_sdk_predict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page(width=200, height=100)
    doc.save(str(pdf))
    doc.close()
    calls: list[tuple[str, bool]] = []

    class FakeOCR:
        def predict(self, path: str, return_word_box: bool):
            calls.append((path, return_word_box))
            return [
                {
                    "page_index": 0,
                    "doc_preprocessor_res": {"output_img_shape": [100, 200, 3]},
                    "rec_texts": ["扫描合同"],
                    "rec_scores": [0.95],
                    "rec_boxes": [[20, 10, 120, 30]],
                    "text_word": [["扫", "描", "合", "同"]],
                    "text_word_boxes": [[[20, 10, 45, 30], [45, 10, 70, 30], [70, 10, 95, 30], [95, 10, 120, 30]]],
                }
            ]

    monkeypatch.setattr(settings, "save_ocr_raw_result", False)

    result = PaddleOCRExtractor(ocr=FakeOCR()).extract(pdf, task_id="TSDK")

    assert calls == [(str(pdf), True)]
    assert result.extractor_used == "paddleocr"
    assert result.document.pages[0].blocks[0].text == "扫描合同"
    assert result.document.pages[0].blocks[0].char_boxes[0].char == "扫"


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
