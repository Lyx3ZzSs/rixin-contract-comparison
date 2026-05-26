from __future__ import annotations

from pathlib import Path

import fitz
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import settings
from app.models import BBox, Document, Page, TextBlock
from app.services.compare_service import CompareService
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.report_generator import build_report_filename


def make_pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4
    y = height - 72
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()


def configure_storage(tmp_path: Path) -> None:
    settings.storage_dir = tmp_path / "storage"
    settings.uploads_dir = settings.storage_dir / "uploads"
    settings.tasks_dir = settings.storage_dir / "tasks"
    settings.highlighted_dir = settings.storage_dir / "highlighted"
    settings.screenshots_dir = settings.storage_dir / "screenshots"
    settings.reports_dir = settings.storage_dir / "reports"
    settings.ocr_dir = settings.storage_dir / "ocr"
    settings.debug_dir = settings.storage_dir / "debug"
    settings.document_extractor = "auto"
    settings.align_structured_extraction = True
    settings.ai_llm_base_url = ""
    settings.ai_llm_api_key = ""
    settings.ai_llm_model = ""
    settings.ensure_storage()


def test_compare_service_generates_artifacts(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    make_pdf(
        original,
        [
            "1. Payment",
            "Buyer shall pay within 30 days.",
            "2. Delivery",
            "Seller shall deliver goods on June 1.",
        ],
    )
    make_pdf(
        compare,
        [
            "1. Payment",
            "Buyer shall pay within 45 days.",
            "2. Delivery",
            "Seller shall deliver goods on June 1.",
            "3. Invoice",
            "Seller shall provide invoice.",
        ],
    )

    service = CompareService()
    task = service.compare(original, compare, task_id="TTEST000001")

    assert task.status == "COMPLETED"
    assert task.stage == "已完成"
    assert task.progress_percent == 100
    assert task.extractor_used == "pymupdf"
    assert task.document_profiles["original"].recommended_strategy == "text"
    assert task.document_profiles["compare"].total_text_chars > 0
    assert Path(task.debug_artifact_paths["document_profiles"]).exists()
    assert Path(task.debug_artifact_paths["clause_matches"]).exists()
    assert Path(task.debug_artifact_paths["diff_decisions"]).exists()
    assert task.diff_count >= 1
    assert any(
        evidence.method == "char_exact"
        for diff in task.diffs
        for evidence in [*diff.original_evidence, *diff.compare_evidence]
    )
    assert Path(task.original_highlight_pdf_path).exists()
    assert Path(task.compare_highlight_pdf_path).exists()
    assert task.report_pdf_path == ""
    assert (settings.tasks_dir / "TTEST000001.json").exists()
    assert any(diff.original_screenshot or diff.compare_screenshot for diff in task.diffs)

    task = service.ensure_report(task)

    assert Path(task.report_pdf_path).exists()
    assert task.report_ai_analysis is None
    assert build_report_filename(task).endswith("差异分析报告.pdf")
    assert task.original_page_screenshots
    assert task.compare_page_screenshots
    assert all(diff.ai_analysis is not None for diff in task.diffs)
    with fitz.open(task.report_pdf_path) as report_pdf:
        report_text = "\n".join(page.get_text() for page in report_pdf)
    assert "差异分析报告" in report_text
    assert "审计统计" in report_text
    assert "合同差异" in report_text
    assert "修改" in report_text


def test_compare_service_aligns_pymupdf_side_to_structured_extraction(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    structured_extractor = FakeStructuredExtractor()
    service = CompareService(structured_extractor=structured_extractor)

    original_result = ExtractionResult(
        document=make_document("original table", "table"),
        extractor_used="auto_ppstructure_ocr_hybrid",
        raw_result_path="/tmp/original_raw.json",
    )
    compare_result = ExtractionResult(
        document=make_document("compare text", "text"),
        extractor_used="pymupdf",
    )

    original_aligned, compare_aligned = service._align_structured_extractions(
        tmp_path / "original.pdf",
        tmp_path / "compare.pdf",
        "TALIGN000001",
        original_result,
        compare_result,
    )

    assert original_aligned is original_result
    assert compare_aligned.extractor_used == "ppstructure_ocr_hybrid"
    assert compare_aligned.document.pages[0].blocks[0].block_type == "table"
    assert compare_aligned.raw_result_path == "/tmp/structured_raw.json"
    assert structured_extractor.calls == [(tmp_path / "compare.pdf", "TALIGN000001")]
    assert "compare 已从 PyMuPDF 切换为结构化 OCR 抽取" in compare_aligned.warnings[0]


def test_compare_service_keeps_pymupdf_when_structured_alignment_fails(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    service = CompareService(structured_extractor=FailingStructuredExtractor())

    original_result = ExtractionResult(
        document=make_document("original table", "table"),
        extractor_used="auto_ppstructure_ocr_hybrid",
    )
    compare_result = ExtractionResult(
        document=make_document("compare text", "text"),
        extractor_used="pymupdf",
    )

    _, compare_aligned = service._align_structured_extractions(
        tmp_path / "original.pdf",
        tmp_path / "compare.pdf",
        "TALIGN000002",
        original_result,
        compare_result,
    )

    assert compare_aligned is compare_result
    assert compare_aligned.extractor_used == "pymupdf"
    assert "compare 尝试切换结构化 OCR 抽取失败，已保留 PyMuPDF 结果" in compare_aligned.warnings[0]


def make_document(text: str, block_type: str) -> Document:
    return Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text=text,
                        bbox=BBox(x0=10, y0=10, x1=100, y1=30),
                        block_type=block_type,
                    )
                ],
            )
        ],
    )


class FakeStructuredExtractor:
    name = "ppstructure_ocr_hybrid"

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str | None]] = []

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        self.calls.append((Path(path), task_id))
        return ExtractionResult(
            document=make_document("structured table", "table"),
            extractor_used="ppstructure_ocr_hybrid",
            raw_result_path="/tmp/structured_raw.json",
        )


class FailingStructuredExtractor:
    name = "ppstructure_ocr_hybrid"

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        raise DocumentExtractionError("remote OCR unavailable")
