from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.config import settings
from app.models import BBox, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.factory import AutoDocumentExtractor, build_document_extractor
from app.services.extractors.paddleocr import PaddleOCRExtractor
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.extractors.ppstructure_ocr_hybrid import PPStructureOCRHybridExtractor
from app.services.extractors.pymupdf import PyMuPDFExtractor


def _pdf(path: Path, width: int = 200, height: int = 100, pages: int = 1) -> Path:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=width, height=height)
    doc.save(str(path))
    doc.close()
    return path


def test_build_document_extractor_defaults_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "document_extractor", "auto")

    extractor = build_document_extractor()

    assert isinstance(extractor, AutoDocumentExtractor)
    assert extractor.name == "auto"


def test_build_document_extractor_supports_pymupdf() -> None:
    extractor = build_document_extractor("pymupdf")

    assert isinstance(extractor, PyMuPDFExtractor)
    assert extractor.name == "pymupdf"


def test_build_document_extractor_supports_ppocrv5() -> None:
    extractor = build_document_extractor("ppocrv5")

    assert isinstance(extractor, PPOCRV5Extractor)
    assert extractor.name == "ppocrv5"


def test_build_document_extractor_supports_ppstructure_ocr_hybrid() -> None:
    extractor = build_document_extractor("ppstructure_ocr_hybrid")

    assert isinstance(extractor, PPStructureOCRHybridExtractor)
    assert extractor.name == "ppstructure_ocr_hybrid"


def test_build_document_extractor_keeps_paddleocr_as_ppocrv5_alias() -> None:
    extractor = build_document_extractor("paddleocr")

    assert isinstance(extractor, PPOCRV5Extractor)
    assert isinstance(PaddleOCRExtractor(), PPOCRV5Extractor)


@pytest.mark.parametrize("name", ["paddleocr_vl", "paddleocrvl", "vl", "vl_ocr_hybrid", "hybrid", "ocr_structure"])
def test_build_document_extractor_rejects_removed_vl_extractors(name: str) -> None:
    with pytest.raises(DocumentExtractionError):
        build_document_extractor(name)


def test_auto_document_extractor_defaults_to_ppocrv5_fallback() -> None:
    extractor = AutoDocumentExtractor()

    assert isinstance(extractor.fallback, PPStructureOCRHybridExtractor)


def test_ppstructure_ocr_hybrid_tags_table_and_keeps_text_after_table() -> None:
    class FakeStructureExtractor:
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
                                    block_id="p1_ppstructure_b1",
                                    page_no=1,
                                    text="表格",
                                    bbox=BBox(x0=50, y0=100, x1=550, y1=240),
                                    block_type="table",
                                ),
                                TextBlock(
                                    block_id="p1_ppstructure_b2",
                                    page_no=1,
                                    text="价格说明",
                                    bbox=BBox(x0=80, y0=260, x1=560, y1=340),
                                    block_type="text",
                                ),
                            ],
                        )
                    ],
                ),
                extractor_used="ppstructure",
                raw_result_path="structure.json",
            )

    class FakeOCRExtractor:
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
                                    block_id="p1_ocr_b1",
                                    page_no=1,
                                    text="6套总合计",
                                    bbox=BBox(x0=260, y0=130, x1=330, y1=150),
                                    block_type="ocr_line",
                                ),
                                TextBlock(
                                    block_id="p1_ocr_b2",
                                    page_no=1,
                                    text="594000",
                                    bbox=BBox(x0=430, y0=130, x1=500, y1=150),
                                    block_type="ocr_line",
                                ),
                                TextBlock(
                                    block_id="p1_ocr_b3",
                                    page_no=1,
                                    text="上述价格为含13%增值税价格，总金额包括光伏功率预测系统V2.0软件部分价格，支",
                                    bbox=BBox(x0=90, y0=270, x1=540, y1=290),
                                    block_type="ocr_line",
                                ),
                            ],
                        )
                    ],
                ),
                extractor_used="ppocrv5",
                raw_result_path="ocr.json",
            )

    result = PPStructureOCRHybridExtractor(FakeStructureExtractor(), FakeOCRExtractor()).extract("scan.pdf")

    blocks = result.document.pages[0].blocks
    assert result.extractor_used == "ppstructure_ocr_hybrid"
    assert [block.block_type for block in blocks] == ["table", "table", "text"]
    assert blocks[0].layout_block_id == "p1_ppstructure_b1"
    assert blocks[2].layout_block_id == "p1_ppstructure_b2"


def test_ppstructure_ocr_hybrid_trusts_structure_table_bbox() -> None:
    class FakeStructureExtractor:
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
                                    block_id="p1_ppstructure_b1",
                                    page_no=1,
                                    text="表格",
                                    bbox=BBox(x0=50, y0=100, x1=560, y1=340),
                                    block_type="table",
                                ),
                            ],
                        )
                    ],
                ),
                extractor_used="ppstructure",
            )

    class FakeOCRExtractor:
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
                                    block_id="p1_ocr_b1",
                                    page_no=1,
                                    text="594000",
                                    bbox=BBox(x0=430, y0=130, x1=500, y1=150),
                                    block_type="ocr_line",
                                ),
                                TextBlock(
                                    block_id="p1_ocr_b2",
                                    page_no=1,
                                    text="上述价格为含13%增值税价格，总金额包括光伏功率预测系统V2.0软件部分价格，支",
                                    bbox=BBox(x0=90, y0=270, x1=540, y1=290),
                                    block_type="ocr_line",
                                ),
                            ],
                        )
                    ],
                ),
                extractor_used="ppocrv5",
            )

    result = PPStructureOCRHybridExtractor(FakeStructureExtractor(), FakeOCRExtractor()).extract("scan.pdf")

    blocks = result.document.pages[0].blocks
    assert [block.block_type for block in blocks] == ["table", "table"]


def test_ppstructure_ocr_hybrid_falls_back_to_ppocrv5_when_structure_fails() -> None:
    class FailingStructureExtractor:
        def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
            raise DocumentExtractionError("layout unavailable")

    class FakeOCRExtractor:
        def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
            return ExtractionResult(
                document=Document(filename="scan.pdf", path="scan.pdf", page_count=1, pages=[]),
                extractor_used="ppocrv5",
            )

    result = PPStructureOCRHybridExtractor(FailingStructureExtractor(), FakeOCRExtractor()).extract("scan.pdf")

    assert result.extractor_used == "ppstructure_ocr_hybrid_ocr_only"
    assert "PP-Structure" in result.warnings[0]


def test_auto_document_extractor_switches_to_ppocrv5() -> None:
    class FailingExtractor:
        def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
            raise DocumentExtractionError("no text")

    class FakePPOCRV5Extractor:
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
                extractor_used="ppocrv5",
            )

    result = AutoDocumentExtractor(primary=FailingExtractor(), fallback=FakePPOCRV5Extractor()).extract("scan.pdf")

    assert result.extractor_used == "auto_ppocrv5"
    assert "结构化 OCR" in result.warnings[0]
    assert "VL" not in result.warnings[0]


def test_ppocrv5_postprocess_marks_margin_lines(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "scan.pdf", width=595, height=842)
    payload = [
        {
            "page_index": 0,
            "doc_preprocessor_res": {"output_img_shape": [842, 595, 3]},
            "rec_texts": ["GXN-26042-00044", "第一条付款方式", "共2页第1页"],
            "rec_boxes": [[40, 20, 180, 36], [72, 120, 260, 140], [260, 810, 340, 828]],
        }
    ]

    document = PPOCRV5Extractor().payload_to_document(payload, pdf)

    blocks = document.pages[0].blocks
    assert [block.block_type for block in blocks] == ["header", "ocr_line", "page_footer"]


def test_ppocrv5_postprocess_does_not_infer_table_from_text(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "scan.pdf", width=595, height=842)
    payload = [
        {
            "page_index": 0,
            "doc_preprocessor_res": {"output_img_shape": [842, 595, 3]},
            "rec_texts": ["序号", "产品名称", "数量", "单价", "金额", "1", "预测服务器", "2", "8500", "17000"],
            "rec_boxes": [
                [60, 120, 90, 138],
                [120, 120, 200, 138],
                [260, 120, 300, 138],
                [340, 120, 380, 138],
                [420, 120, 460, 138],
                [60, 150, 80, 168],
                [120, 150, 210, 168],
                [260, 150, 280, 168],
                [340, 150, 390, 168],
                [420, 150, 475, 168],
            ],
        }
    ]

    document = PPOCRV5Extractor().payload_to_document(payload, pdf)

    assert {block.block_type for block in document.pages[0].blocks} == {"ocr_line"}


def test_ppocrv5_corrects_verified_per_mille_ocr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "scan.pdf", width=240, height=100)
    text = "物价款的3%0作为违约金"
    payload = [
        {
            "page_index": 0,
            "doc_preprocessor_res": {"output_img_shape": [100, 240, 3]},
            "rec_texts": [text],
            "rec_boxes": [[0, 10, 220, 30]],
            "text_word": [list(text)],
            "text_word_region": [
                [[index * 10, 10, (index + 1) * 10, 30] for index in range(len(text))]
            ],
        }
    ]
    extractor = PPOCRV5Extractor()
    monkeypatch.setattr(extractor, "_pdf_page_texts", lambda path: ["物价款的3‰作为违约金"])

    document = extractor.payload_to_document(payload, pdf)

    block = document.pages[0].blocks[0]
    assert block.text == "物价款的3‰作为违约金"
    assert [char_box.char for char_box in block.char_boxes] == list(block.text)
    assert [char_box.text_index for char_box in block.char_boxes] == list(range(len(block.text)))
    per_mille_box = block.char_boxes[5]
    assert per_mille_box.char == "‰"
    assert per_mille_box.bbox.x0 == 50
    assert per_mille_box.bbox.x1 == 70


def test_ppocrv5_does_not_guess_unverified_per_mille_ocr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "scan.pdf", width=240, height=100)
    payload = [
        {
            "page_index": 0,
            "doc_preprocessor_res": {"output_img_shape": [100, 240, 3]},
            "rec_texts": ["完成率100%0缺陷"],
            "rec_boxes": [[0, 10, 180, 30]],
        }
    ]
    extractor = PPOCRV5Extractor()
    monkeypatch.setattr(extractor, "_pdf_page_texts", lambda path: ["完成率100%0缺陷"])

    document = extractor.payload_to_document(payload, pdf)

    assert document.pages[0].blocks[0].text == "完成率100%0缺陷"


def test_ppocrv5_postprocess_keeps_price_explanation_as_ocr_lines(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "scan.pdf", width=595, height=842)
    payload = [
        {
            "page_index": 0,
            "doc_preprocessor_res": {"output_img_shape": [842, 595, 3]},
            "rec_texts": [
                "小计",
                "12000",
                "1套总计",
                "99000",
                "2套合计",
                "198000",
                "6套总合计",
                "594000",
                "上述价格为含13%增值税价格，总金额包括光伏功率预测系统V2.0软件部分价格，支",
                "持该系统所需的硬件设备价格。该价格为固定不变价，包括设备及随机附件的设计、采购、",
            ],
            "rec_boxes": [
                [220, 100, 260, 120],
                [430, 100, 480, 120],
                [215, 126, 270, 146],
                [430, 126, 480, 146],
                [215, 152, 270, 172],
                [430, 152, 490, 172],
                [210, 178, 275, 198],
                [430, 178, 490, 198],
                [90, 225, 540, 245],
                [65, 252, 535, 272],
            ],
        }
    ]

    document = PPOCRV5Extractor().payload_to_document(payload, pdf)
    blocks = document.pages[0].blocks

    assert blocks[6].block_type == "ocr_line"
    assert blocks[7].block_type == "ocr_line"
    assert blocks[8].block_type == "ocr_line"
    assert blocks[9].block_type == "ocr_line"


def test_ppocrv5_postprocess_keeps_normal_clause_lines(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "scan.pdf", width=595, height=842)
    payload = [
        {
            "page_index": 0,
            "doc_preprocessor_res": {"output_img_shape": [842, 595, 3]},
            "rec_texts": ["第一条付款方式", "买方应在30日内付款。", "合同总价为100万元。"],
            "rec_boxes": [[72, 120, 220, 140], [72, 150, 260, 168], [72, 180, 240, 198]],
        }
    ]

    document = PPOCRV5Extractor().payload_to_document(payload, pdf)

    assert [block.block_type for block in document.pages[0].blocks] == ["ocr_line", "ocr_line", "ocr_line"]


def test_ppocrv5_postprocess_filters_low_confidence_edge_noise(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "scan.pdf", width=595, height=842)
    payload = [
        {
            "page_index": 0,
            "doc_preprocessor_res": {"output_img_shape": [842, 595, 3]},
            "rec_texts": ["取维护费。", "心"],
            "rec_scores": [0.99, 0.11],
            "rec_boxes": [[65, 63, 121, 78], [0, 533, 7, 604]],
        }
    ]

    document = PPOCRV5Extractor().payload_to_document(payload, pdf)

    assert [block.text for block in document.pages[0].blocks] == ["取维护费。"]


def test_build_document_extractor_rejects_unsupported_name() -> None:
    with pytest.raises(DocumentExtractionError):
        build_document_extractor("unknown")
