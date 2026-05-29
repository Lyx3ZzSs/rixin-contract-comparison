from __future__ import annotations

import fitz

from app.models import BBox, CompareTask, DiffItem, EvidenceBox
from app.services.report_generator import ReportGenerator


def test_report_generator_marks_source_paragraph_and_before_after_text(tmp_path) -> None:
    task = CompareTask(
        task_id="TREPORT001",
        status="COMPLETED",
        original_filename="original.pdf",
        compare_filename="compare.pdf",
        diffs=[
            DiffItem(
                diff_id="D001",
                diff_type="MODIFY",
                clause_no="1",
                title="付款",
                original_snippet="30 days",
                compare_snippet="45 days",
                original_evidence=[
                    EvidenceBox(page_no=1, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="30 days", highlight_type="MODIFY"),
                ],
                compare_evidence=[
                    EvidenceBox(page_no=2, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="45 days", highlight_type="MODIFY"),
                ],
            )
        ],
    )
    output_path = tmp_path / "report.pdf"

    ReportGenerator().generate(task, output_path)

    with fitz.open(output_path) as report_pdf:
        report_text = "\n".join(page.get_text() for page in report_pdf)
    assert "来源段落" in report_text
    assert "原文" in report_text
    assert "修改后" in report_text
    assert "1 付款" in report_text
    assert "30 days" in report_text
    assert "45 days" in report_text
