from __future__ import annotations

import fitz
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.models import BBox, CompareTask, DiffItem, EvidenceBox
from app.services.report_generator import ReportGenerator


def _make_pdf(path, page_count: int = 6) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for page_no in range(1, page_count + 1):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 760, f"fixture page {page_no}")
        pdf.drawString(72, 720, "30 days 45 days invoice warranty")
        pdf.showPage()
    pdf.save()


def test_report_generator_marks_source_paragraph_and_before_after_text(tmp_path) -> None:
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _make_pdf(original_pdf)
    _make_pdf(compare_pdf)

    task = CompareTask(
        task_id="TREPORT001",
        status="COMPLETED",
        original_filename="original.pdf",
        compare_filename="compare.pdf",
        original_pdf_path=str(original_pdf),
        compare_pdf_path=str(compare_pdf),
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
            ),
            DiffItem(
                diff_id="D002",
                diff_type="ADD",
                clause_no="2",
                title="发票",
                compare_snippet="新增发票条款",
                compare_evidence=[
                    EvidenceBox(page_no=3, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="新增发票条款", highlight_type="ADD"),
                ],
            ),
            DiffItem(
                diff_id="D003",
                diff_type="DELETE",
                clause_no="3",
                title="旧质保",
                original_snippet="旧质保条款",
                original_evidence=[
                    EvidenceBox(page_no=4, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="旧质保条款", highlight_type="DELETE"),
                ],
            ),
            DiffItem(
                diff_id="D004",
                diff_type="DELETE",
                title="无定位表格项",
                original_text="3 | 短期模型 | 光伏场短期功率预报 模型开发。 | 国能日新 | 套 | 1",
                original_snippet="3 | 短期模型 | 光伏场短期功率预报 模型开发。 | 国能日新 | 套 | 1",
                original_evidence=[],
                compare_evidence=[],
            ),
            DiffItem(
                diff_id="D005",
                diff_type="MODIFY",
                clause_no="5",
                title="混合改动",
                original_evidence=[
                    EvidenceBox(page_no=5, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="旧说明", highlight_type="DELETE"),
                    EvidenceBox(page_no=5, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="30 days", highlight_type="MODIFY"),
                ],
                compare_evidence=[
                    EvidenceBox(page_no=6, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="新增说明", highlight_type="ADD"),
                    EvidenceBox(page_no=6, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="45 days", highlight_type="MODIFY"),
                ],
            ),
        ],
    )
    output_path = tmp_path / "report.pdf"

    ReportGenerator().generate(task, output_path)

    with fitz.open(output_path) as report_pdf:
        report_text = "\n".join(page.get_text() for page in report_pdf)
        image_count = sum(len(page.get_images()) for page in report_pdf)
    assert "审计统计与差异概览" in report_text
    assert "差异明细" in report_text
    assert "来源段落" in report_text
    assert "原文" in report_text
    assert "修改后" in report_text
    assert "1 付款" in report_text
    assert "30 days" in report_text
    assert "45 days" in report_text
    assert "2 发票" in report_text
    assert "原文无对应内容" in report_text
    assert "新增发票条款" in report_text
    assert "3 旧质保" in report_text
    assert "旧质保条款" in report_text
    assert "新版已删除" in report_text
    assert "差异索引" in report_text
    assert "差异类型与证据说明" in report_text
    assert "未定位/需复核差异附录" not in report_text
    assert "高质量证据" not in report_text
    assert "需复核" not in report_text
    assert "D004" not in report_text
    assert "无定位表格项" not in report_text
    assert "D005:ADD" in report_text
    assert "D005:DELETE" in report_text
    assert "D005:MODIFY" in report_text
    assert "新增说明" in report_text
    assert "旧说明" in report_text
    assert image_count > 0
