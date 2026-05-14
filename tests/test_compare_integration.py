from __future__ import annotations

from pathlib import Path

import fitz
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import settings
from app.services.compare_service import CompareService
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
    settings.document_extractor = "auto"
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
    task = service.compare(original, compare, enable_ai_analysis=False, task_id="TTEST000001")

    assert task.status == "COMPLETED"
    assert task.extractor_used == "pymupdf"
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
    assert all(diff.ai_analysis is None for diff in task.diffs)
    with fitz.open(task.report_pdf_path) as report_pdf:
        report_text = "\n".join(page.get_text() for page in report_pdf)
    assert "差异分析报告" in report_text
    assert "审计统计" in report_text
    assert "合同差异" in report_text
    assert "修改" in report_text
