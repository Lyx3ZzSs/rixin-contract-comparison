from __future__ import annotations

import fitz
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.models import AuditItemReview, BBox, CompareTask, DiffItem, EvidenceBox, TextRange
from app.services.report_generator import ReportGenerator, _SOURCE_TYPE_LABELS, _SOURCE_TYPE_ORDER


def _make_pdf(path, page_count: int = 6) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for page_no in range(1, page_count + 1):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 760, f"fixture page {page_no}")
        pdf.drawString(72, 720, "30 days 45 days invoice warranty")
        pdf.showPage()
    pdf.save()


def test_report_generator_orders_supported_source_types() -> None:
    assert "signature" not in _SOURCE_TYPE_LABELS
    assert "signature" not in _SOURCE_TYPE_ORDER
    assert _SOURCE_TYPE_ORDER["table"] < _SOURCE_TYPE_ORDER["seal"]
    assert _SOURCE_TYPE_ORDER["seal"] < _SOURCE_TYPE_ORDER["signing_region"]
    assert _SOURCE_TYPE_ORDER["signing_region"] < _SOURCE_TYPE_ORDER["header_footer"]
    assert _SOURCE_TYPE_LABELS["signing_region"] == "签章区"


def test_report_generator_produces_grouped_tables_with_diff_content(tmp_path) -> None:
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
            DiffItem(
                diff_id="D006",
                diff_type="MODIFY",
                clause_no="6",
                title="忽略混合改动",
                original_evidence=[
                    EvidenceBox(page_no=5, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="忽略旧说明", highlight_type="DELETE"),
                    EvidenceBox(page_no=5, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="10 days", highlight_type="MODIFY"),
                ],
                compare_evidence=[
                    EvidenceBox(page_no=6, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="忽略新增说明", highlight_type="ADD"),
                    EvidenceBox(page_no=6, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="20 days", highlight_type="MODIFY"),
                ],
            ),
        ],
        audit_item_reviews={
            "D002:ADD": AuditItemReview(review_status="IGNORED"),
            "D006:DELETE": AuditItemReview(review_status="IGNORED"),
        },
    )
    output_path = tmp_path / "report.pdf"

    ReportGenerator().generate(task, output_path)

    with fitz.open(output_path) as report_pdf:
        report_text = "\n".join(page.get_text() for page in report_pdf)

    assert "审计统计与差异概览" in report_text
    assert "差异明细" in report_text
    assert "条款差异" in report_text
    assert "原文内容" in report_text
    assert "修改后内容" in report_text
    assert "1 付款" in report_text
    assert "30 days" in report_text
    assert "45 days" in report_text
    assert "2 发票" not in report_text
    assert "新增发票条款" not in report_text
    assert "3 旧质保" in report_text
    assert "旧质保条款" in report_text
    assert "差异类型与证据说明" in report_text
    assert "D004" not in report_text
    assert "无定位表格项" not in report_text
    assert "D006:DELETE" not in report_text
    assert "忽略混合改动" in report_text
    assert "忽略旧说明" not in report_text
    assert "忽略新增说明" in report_text
    assert "新增说明" in report_text
    assert "旧说明" in report_text


def test_report_generator_handles_truncated_highlighted_text(tmp_path) -> None:
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _make_pdf(original_pdf)
    _make_pdf(compare_pdf)
    compare_text = (
        "乙方 单位名称: 国能日新科技股份有限公司 (章) 法定代表人或授权代表签字: "
        "内 1幢二层 227 号 电话:010-83458100 开户银行: 招商银行北京大屯路支行 "
        "税号:911101086723891430 帐号:110904199110901 联系人及电话: 李伟18991278230 "
        "签字日期: 年 月 日 发电计划, 并上传预测数据至省调及西安集控中心。"
    )
    task = CompareTask(
        task_id="TREPORT_TRUNCATED",
        status="COMPLETED",
        original_filename="original.pdf",
        compare_filename="compare.pdf",
        original_pdf_path=str(original_pdf),
        compare_pdf_path=str(compare_pdf),
        diffs=[
            DiffItem(
                diff_id="D020",
                diff_type="ADD",
                title="乙方",
                compare_text=compare_text,
                compare_evidence=[
                    EvidenceBox(
                        page_no=1,
                        bbox=BBox(x0=1, y0=2, x1=3, y1=4),
                        text=compare_text,
                        highlight_type="ADD",
                    ),
                ],
                compare_change_ranges=[TextRange(start=0, end=176)],
            ),
        ],
    )

    output_path = tmp_path / "report.pdf"

    ReportGenerator().generate(task, output_path)

    assert output_path.exists()
