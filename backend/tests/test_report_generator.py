from __future__ import annotations

import fitz
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from app.models import (
    AuditItemReview,
    BBox,
    CompareTask,
    DiffItem,
    EvidenceBox,
    OcrRemediationAction,
    PageOcrQualityProfile,
    TaskOcrQualitySummary,
    TaskOcrRemediationSummary,
    TextRange,
)
from app.services.report_generator import (
    ReportGenerator,
    _SOURCE_TYPE_LABELS,
    _SOURCE_TYPE_ORDER,
    _visible_audit_items,
)
from app.services.audit_summary import build_task_audit_items


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
    assert _SOURCE_TYPE_ORDER["page"] < _SOURCE_TYPE_ORDER["signing_region"]
    assert _SOURCE_TYPE_ORDER["signing_region"] < _SOURCE_TYPE_ORDER["table"]
    assert _SOURCE_TYPE_ORDER["signing_region"] < _SOURCE_TYPE_ORDER["seal"]
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
                    EvidenceBox(
                        page_no=3, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="新增发票条款", highlight_type="ADD"
                    ),
                ],
            ),
            DiffItem(
                diff_id="D003",
                diff_type="DELETE",
                clause_no="3",
                title="旧质保",
                original_snippet="旧质保条款",
                original_evidence=[
                    EvidenceBox(
                        page_no=4, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="旧质保条款", highlight_type="DELETE"
                    ),
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
                    EvidenceBox(
                        page_no=5, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="忽略旧说明", highlight_type="DELETE"
                    ),
                    EvidenceBox(page_no=5, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="10 days", highlight_type="MODIFY"),
                ],
                compare_evidence=[
                    EvidenceBox(
                        page_no=6, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="忽略新增说明", highlight_type="ADD"
                    ),
                    EvidenceBox(page_no=6, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="20 days", highlight_type="MODIFY"),
                ],
            ),
        ],
        audit_item_reviews={
            "D002:ADD": AuditItemReview(review_status="IGNORED"),
            "D006:DELETE": AuditItemReview(review_status="IGNORED"),
            "D006:ADD": AuditItemReview(review_status="CONFIRMED", review_comment="新增内容有效"),
            "D006:MODIFY": AuditItemReview(review_status="NEEDS_REVIEW", review_comment="期限需复核"),
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
    assert "无定位表格项" in report_text
    assert "短期模型" in report_text
    assert "D006:DELETE" not in report_text
    assert "忽略混合改动" in report_text
    assert "忽略旧说明" not in report_text
    assert "忽略新增说明" in report_text
    assert "新增说明" in report_text
    assert "旧说明" in report_text
    add_detail_start = report_text.index("D006:ADD")
    modify_detail_start = report_text.index("D006:MODIFY")
    assert add_detail_start < modify_detail_start
    assert "审核状态：CONFIRMED" in report_text[add_detail_start:modify_detail_start]
    assert "审核意见：新增内容有效" in report_text[add_detail_start:modify_detail_start]
    assert "审核状态：NEEDS_REVIEW" in report_text[modify_detail_start:]
    assert "审核意见：期限需复核" in report_text[modify_detail_start:]


def test_report_visibility_uses_normalized_legacy_audit_item_reviews() -> None:
    ignored_diff = DiffItem(
        diff_id="DLEGACYIGNORED",
        diff_type="MODIFY",
        review_status="IGNORED",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=1, y0=2, x1=3, y1=4),
                text="old",
                highlight_type="MODIFY",
            )
        ],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=1, y0=2, x1=3, y1=4),
                text="new",
                highlight_type="MODIFY",
            )
        ],
    )

    assert _visible_audit_items(CompareTask(task_id="TLEGACYREPORT", diffs=[ignored_diff])) == []


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


def test_report_renders_complete_located_and_unlocated_audit_item_evidence(tmp_path) -> None:
    task = CompareTask(
        task_id="TCOMPLETEEVIDENCE",
        status="COMPLETED",
        original_filename="original.pdf",
        compare_filename="compare.pdf",
        diffs=[
            DiffItem(
                diff_id="DLOCATED",
                diff_type="MODIFY",
                source_type="table",
                section_type="appendix",
                section_path=["附件一", "报价表"],
                title="报价调整",
                original_text="原价 100 元",
                compare_text="新价 120 元",
                original_evidence=[
                    EvidenceBox(
                        page_no=3,
                        bbox=BBox(x0=10, y0=20, x1=80, y1=35),
                        method="char_exact",
                        text="原价 100 元",
                        highlight_type="MODIFY",
                        confidence=0.97,
                        evidence_quality="HIGH",
                    ),
                    EvidenceBox(
                        page_no=-1,
                        bbox=BBox(x0=1, y0=2, x1=3, y1=4),
                        method="invalid_negative_page",
                        text="无效定位不得展示",
                        highlight_type="MODIFY",
                    ),
                ],
                compare_evidence=[
                    EvidenceBox(
                        page_no=4,
                        bbox=BBox(x0=12, y0=22, x1=82, y1=37),
                        method="ocr_exact",
                        text="新价 120 元",
                        highlight_type="MODIFY",
                        confidence=0.91,
                        evidence_quality="MEDIUM",
                    )
                ],
                quality_status="NEEDS_REVIEW",
                structural_flags=["TABLE_STRUCTURE"],
                review_flags=["LOW_MATCH_CONFIDENCE"],
                text_confidence=0.73,
                match_confidence="LOW",
            ),
            DiffItem(
                diff_id="DUNLOCATED",
                diff_type="DELETE",
                source_type="clause",
                section_type="main_contract",
                section_path=["第二章", "交付"],
                title="无定位交付要求",
                original_text="应在十日内交付",
                original_evidence=[
                    EvidenceBox(
                        page_no=0,
                        bbox=BBox(x0=0, y0=0, x1=0, y1=0),
                        method="text_fallback",
                        text="应在十日内交付",
                        highlight_type="DELETE",
                    )
                ],
                match_confidence="NORMAL",
            ),
            DiffItem(
                diff_id="DIGNORED",
                diff_type="ADD",
                title="明确忽略项",
                compare_text="不得出现在报告",
            ),
        ],
        audit_item_reviews={
            "DLOCATED:MODIFY": AuditItemReview(
                review_status="NEEDS_REVIEW",
                review_comment="请复核报价 <script>alert(1)</script>",
                reviewed_by="auditor-a",
            ),
            "DUNLOCATED:DELETE": AuditItemReview(
                review_status="UNREVIEWED",
            ),
            "DIGNORED:ADD": AuditItemReview(review_status="IGNORED"),
        },
        ocr_quality_summary=TaskOcrQualitySummary(
            requires_review=True,
            profiles=[
                PageOcrQualityProfile(
                    side="compare",
                    page_no=4,
                    status="UNRELIABLE",
                    reasons=["OCR_TABLE_AMBIGUOUS"],
                    affected_diff_ids=["DLOCATED"],
                )
            ],
        ),
        ocr_remediation_summary=TaskOcrRemediationSummary(
            actions=[
                OcrRemediationAction(
                    action_id="compare:4:DLOCATED:ESCALATE_MANUAL_REVIEW",
                    action_type="ESCALATE_MANUAL_REVIEW",
                    reason="TABLE_UNRELIABLE",
                    status="MANUAL_REVIEW_REQUIRED",
                    side="compare",
                    page_no=4,
                    diff_id="DLOCATED",
                    changed_evidence=True,
                )
            ]
        ),
    )
    output_path = tmp_path / "complete-evidence.pdf"

    ReportGenerator().generate(task, output_path)

    with fitz.open(output_path) as report_pdf:
        report_text = "\n".join(page.get_text() for page in report_pdf)

    # Located canonical item: business identity, both texts and precise evidence.
    assert "DLOCATED:MODIFY" in report_text
    assert "来源：表格 (table)" in report_text
    assert "章节类型：appendix" in report_text
    assert "章节路径：附件一 / 报价表" in report_text
    assert "原价 100 元" in report_text
    assert "新价 120 元" in report_text
    assert "原文第3页" in report_text
    assert "bbox=(10, 20, 80, 35)" in report_text
    assert "新版第4页" in report_text
    assert "bbox=(12, 22, 82, 37)" in report_text
    assert "char_exact" in report_text
    assert "ocr_exact" in report_text
    assert "invalid_negative_page" not in report_text
    assert "无效定位不得展示" not in report_text
    assert "原文第-1页" not in report_text

    # Quality, OCR, remediation and independent review context are never inferred away.
    assert "质量状态：NEEDS_REVIEW" in report_text
    assert "结构标记：TABLE_STRUCTURE" in report_text
    assert "审核标记：LOW_MATCH_CONFIDENCE" in report_text
    assert "文本置信度：0.73" in report_text
    assert "匹配置信度：LOW" in report_text
    assert "OCR受影响：是" in report_text
    assert "OCR状态：UNRELIABLE" in report_text
    assert "OCR原因：OCR_TABLE_AMBIGUOUS" in report_text
    assert "OCR侧：compare" in report_text
    assert "OCR页码：4" in report_text
    assert "修复动作ID：compare:4:DLOCATED:ESCALATE_MANUAL_REVIEW" in report_text
    assert "修复动作：ESCALATE_MANUAL_REVIEW" in report_text
    assert "修复状态：MANUAL_REVIEW_REQUIRED" in report_text
    assert "OCR归属：审计项" in report_text
    assert "修复归属：审计项" in report_text
    assert "审核状态：NEEDS_REVIEW" in report_text
    assert "审核意见：请复核报价 <script>alert(1)</script>" in report_text

    # Unlocated/fallback items remain visible, including empty OCR/remediation context.
    assert "DUNLOCATED:DELETE" in report_text
    assert "无定位交付要求" in report_text
    assert "应在十日内交付" in report_text
    assert "证据位置：未定位" in report_text
    assert "EVIDENCE_UNLOCATED" in report_text
    assert "审核状态：UNREVIEWED" in report_text
    assert "审核意见：无" in report_text

    assert "DIGNORED:ADD" not in report_text
    assert "不得出现在报告" not in report_text


def test_report_generates_from_legacy_very_long_context_without_layout_error(tmp_path) -> None:
    long_unbroken_ascii = "ASCII-BEGIN-" + ("ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 2_000) + "-ASCII-END"
    literal_entity = "ENTITY-BEGIN-&amp;-ENTITY-END"
    emoji_zwj = "EMOJI-BEGIN-👩‍⚖️-EMOJI-END"
    long_comment = (
        "LEGACY-COMMENT-BEGIN-"
        + ("历史意见" * 20_000)
        + long_unbroken_ascii
        + literal_entity
        + emoji_zwj
        + "-LEGACY-COMMENT-END"
    )
    long_path = "PATH-BEGIN-" + ("超长章节" * 2_000) + "-PATH-END"
    long_structural_flag = "STRUCT-BEGIN-" + ("结构" * 2_000) + "-STRUCT-END"
    long_review_flag = "FLAG-BEGIN-" + ("复核" * 2_000) + "-FLAG-END"
    task = CompareTask(
        task_id="TLONGREPORT",
        status="COMPLETED",
        original_filename="original.pdf",
        compare_filename="compare.pdf",
        diffs=[
            DiffItem(
                diff_id="DLONG",
                diff_type="MODIFY",
                title="超长历史审核",
                section_path=[long_path],
                structural_flags=[long_structural_flag],
                review_flags=[long_review_flag],
                original_text="原文完整内容",
                compare_text="修改后完整内容",
            )
        ],
        audit_item_reviews={"DLONG:MODIFY": AuditItemReview(review_status="NEEDS_REVIEW", review_comment=long_comment)},
    )
    output_path = tmp_path / "long-report.pdf"

    generator = ReportGenerator()
    try:
        generator.generate(task, output_path)
    except Exception as exc:
        pytest.fail(f"legacy long report must paginate instead of raising {type(exc).__name__}: {exc}")

    with fitz.open(output_path) as report_pdf:
        report_text = "\n".join(page.get_text() for page in report_pdf)
        page_count = len(report_pdf)
    compact_report_text = "".join(report_text.split())
    assert page_count > 1
    assert "LEGACY-COMMENT-BEGIN" in compact_report_text
    assert "LEGACY-COMMENT-END" in compact_report_text
    assert "PATH-BEGIN" in compact_report_text
    assert "PATH-END" in compact_report_text
    assert "STRUCT-BEGIN" in compact_report_text
    assert "STRUCT-END" in compact_report_text
    assert "FLAG-BEGIN" in compact_report_text
    assert "FLAG-END" in compact_report_text
    assert "ASCII-BEGIN" in compact_report_text
    assert "ASCII-END" in compact_report_text
    assert literal_entity in compact_report_text
    assert compact_report_text.index("EMOJI-BEGIN") < compact_report_text.index("EMOJI-END")

    item = build_task_audit_items(task)[0]
    detail_flowables = generator._audit_item_detail_flowables(item, generator._styles(generator._font_name))
    assert len(detail_flowables) == len(generator._audit_item_detail_fields(item))
    detail_text = "\n".join(flowable.getPlainText() for flowable in detail_flowables if isinstance(flowable, Paragraph))
    assert "ASCII-BEGIN" in detail_text and "ASCII-END" in detail_text
    assert literal_entity in detail_text
    assert emoji_zwj in detail_text
