from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, CharBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.diff_quality import DiffQualityProcessor
from app.services.text_evidence import EvidenceConsensusEvaluator, TextEvidenceEquivalence


def test_date_separator_missing_glyph_is_contextually_equivalent() -> None:
    decision = TextEvidenceEquivalence().compare("签订日期：2026□7□1", "签订日期：2026年7月1日")

    assert decision.equivalent
    assert decision.reason == "DATE_SEPARATOR_GLYPH_NOISE"
    assert decision.original_raw == "签订日期：2026□7□1"
    assert decision.compare_raw == "签订日期：2026年7月1日"
    assert decision.original_normalized == decision.compare_normalized


def test_blank_date_connector_glyph_is_equivalent_in_date_context() -> None:
    decision = TextEvidenceEquivalence().compare(
        "2026年 月 □",
        "2026年 月 日",
        context="封面字段：签订日期",
    )

    assert decision.equivalent
    assert decision.reason == "DATE_SEPARATOR_GLYPH_NOISE"


def test_date_range_layout_is_equivalent_but_changed_date_is_not() -> None:
    equivalent = TextEvidenceEquivalence().compare(
        "本合同自 2026 7 1\n2029 6 30 履行",
        "本合同自2026年7月1日至2029年6月30日履行",
    )
    changed = TextEvidenceEquivalence().compare(
        "本合同自 2026 7 1\n2029 6 30 履行",
        "本合同自2026年7月2日至2029年6月30日履行",
    )

    assert equivalent.equivalent
    assert equivalent.reason == "DATE_SEPARATOR_GLYPH_NOISE"
    assert not changed.equivalent


def test_square_glyph_is_not_globally_removed() -> None:
    decision = TextEvidenceEquivalence().compare("账户□名称", "账户名称")

    assert not decision.equivalent
    assert decision.reason == "NOT_EQUIVALENT"


def test_leading_bullet_attachment_is_equivalent_without_dropping_content() -> None:
    decision = TextEvidenceEquivalence().compare("● 服务范围", "服务范围")

    assert decision.equivalent
    assert decision.reason == "BULLET_ATTACHMENT_NOISE"

    changed = TextEvidenceEquivalence().compare("● 服务范围", "付款范围")
    assert not changed.equivalent


def test_embedded_bullet_attachment_is_equivalent_only_when_content_matches() -> None:
    decision = TextEvidenceEquivalence().compare("第一行\n第二行", "●第一行\n第二●行")

    assert decision.equivalent
    assert decision.reason == "BULLET_ATTACHMENT_NOISE"


def test_blank_form_underscore_style_is_equivalent_but_variable_is_not() -> None:
    equivalent = TextEvidenceEquivalence().compare("签字：________", "签字：____")
    variable = TextEvidenceEquivalence().compare("变量：_rate", "变量：rate")

    assert equivalent.equivalent
    assert equivalent.reason == "FORM_UNDERSCORE_EQUIVALENT"
    assert not variable.equivalent


def test_single_numeric_format_underscore_is_equivalent() -> None:
    decision = TextEvidenceEquivalence().compare("发票后_30个工作日内支付", "发票后30个工作日内支付")

    assert decision.equivalent
    assert decision.reason == "FORM_UNDERSCORE_EQUIVALENT"


def test_blank_form_slash_placeholder_is_contextually_equivalent() -> None:
    equivalent = TextEvidenceEquivalence().compare("进度款：￥__/元", "进度款：￥/元")
    semantic = TextEvidenceEquivalence().compare("适用A/B方案", "适用AB方案")

    assert equivalent.equivalent
    assert equivalent.reason == "FORM_SLASH_PLACEHOLDER_EQUIVALENT"
    assert not semantic.equivalent


def test_standalone_form_slash_line_is_equivalent() -> None:
    decision = TextEvidenceEquivalence().compare("技术标准：\n/\n如无特别要求", "技术标准：\n如无特别要求")

    assert decision.equivalent
    assert decision.reason == "FORM_SLASH_PLACEHOLDER_EQUIVALENT"


def test_formula_subscript_position_is_equivalent() -> None:
    decision = TextEvidenceEquivalence().compare("变量 CO₂ 的值", "变量 CO2 的值")

    assert decision.equivalent
    assert decision.reason == "FORMULA_SCRIPT_POSITION_EQUIVALENT"


def test_formula_index_ocr_omission_uses_repeated_formula_context() -> None:
    decision = TextEvidenceEquivalence().compare(
        "准确率算法为：其中PMi为i时刻可用功率，PMi为实际功率，Ppi为预测功率",
        "准确率算法为：其中PM为i时刻可用功率，PMi为实际功率，PPi为预测功率",
    )

    assert decision.equivalent
    assert decision.reason == "FORMULA_SCRIPT_POSITION_EQUIVALENT"


def test_embedded_word_revision_line_is_removed_without_touching_real_delete_text() -> None:
    original = "第二条 合同价款\n删除[赵培淞]: 贰万叁仟\n甲方有权删除错误数据。"
    compare = "第二条 合同价款\n甲方有权删除错误数据。"

    decision = TextEvidenceEquivalence().compare(original, compare)
    real_change = TextEvidenceEquivalence().compare("甲方有权删除错误数据。", "甲方有权修改错误数据。")

    assert decision.equivalent
    assert decision.reason == "WORD_REVISION_ARTIFACT"
    assert not real_change.equivalent


def test_percent_and_per_mille_are_never_text_normalized_as_equal() -> None:
    decision = TextEvidenceEquivalence().compare("违约金为5%", "违约金为5‰")

    assert not decision.equivalent
    assert decision.reason == "PROTECTED_PERCENT_UNIT_CHANGE"
    assert decision.original_normalized != decision.compare_normalized


def test_visual_consensus_suppresses_false_percent_per_mille_diff(tmp_path: Path) -> None:
    original = _percent_document(tmp_path / "original.pdf", text="5%作为违约金", loop_count=3)
    compare = _percent_document(tmp_path / "compare.pdf", text="5‰作为违约金", loop_count=3)
    diff = _percent_diff()

    decision = EvidenceConsensusEvaluator().assess(diff, original, compare)

    assert decision.verdict == "EQUIVALENT"
    assert decision.reason == "PERCENT_UNIT_VISUAL_CONSENSUS"
    assert decision.sources == ["original:ocr", "original:visual", "compare:ocr", "compare:visual"]


def test_reliable_visual_percent_per_mille_change_remains_confirmed(tmp_path: Path) -> None:
    original = _percent_document(tmp_path / "original.pdf", text="5%作为违约金", loop_count=2)
    compare = _percent_document(tmp_path / "compare.pdf", text="5‰作为违约金", loop_count=3)
    diff = _percent_diff()

    decision = EvidenceConsensusEvaluator().assess(diff, original, compare)

    assert decision.verdict == "DIFFERENT"
    assert decision.reason == "PERCENT_UNIT_VISUAL_DIFFERENCE_CONFIRMED"


def test_diff_quality_suppresses_percent_unit_when_visual_sources_agree(tmp_path: Path) -> None:
    original = _percent_document(tmp_path / "original.pdf", text="5%作为违约金", loop_count=3)
    compare = _percent_document(tmp_path / "compare.pdf", text="5‰作为违约金", loop_count=3)

    result = DiffQualityProcessor().process(
        [_percent_diff()],
        original_document=original,
        compare_document=compare,
    )

    assert result.diffs == []
    assert any(
        item.action == "suppressed_noise" and item.detail["reason"] == "PERCENT_UNIT_VISUAL_CONSENSUS"
        for item in result.decisions
    )


def test_diff_quality_marks_unreliable_critical_amount_as_review_required() -> None:
    diff = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        source_type="clause",
        original_text="合同金额10000元",
        compare_text="合同金额100000元",
        original_snippet="10000元",
        compare_snippet="100000元",
        review_flags=[
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_AMOUNT_CHANGE",
            "PAGE_UNRELIABLE",
            "OCR_LOW_CONFIDENCE",
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert len(result.diffs) == 1
    assert result.diffs[0].quality_status == "NEEDS_REVIEW"
    assert "CRITICAL_NUMERIC_SOURCE_CONFLICT" in result.diffs[0].review_flags
    assert any(
        item.action == "review_required" and item.detail["reason"] == "CRITICAL_NUMERIC_SOURCE_CONFLICT"
        for item in result.decisions
    )


def test_critical_identifier_shared_ambiguous_candidates_are_suppressed() -> None:
    original = _identifier_document(
        "91610103MABOHNNLOU",
        [("ppocrv5", "91610103MABOHNNLOU"), ("ppstructure", "91610103MAB0HNNLOU")],
    )
    compare = _identifier_document(
        "91610103MAB0HNNLOU",
        [("ppocrv5", "91610103MAB0HNNLOU"), ("ppstructure", "91610103MABOHNNLOU")],
    )
    diff = _identifier_diff()

    result = DiffQualityProcessor().process([diff], original_document=original, compare_document=compare)

    assert result.diffs == []
    decision = next(item for item in result.decisions if item.action == "suppressed_noise")
    assert decision.detail["reason"] == "CRITICAL_IDENTIFIER_SHARED_AMBIGUOUS_CANDIDATES"
    assert decision.detail["field_type"] == "credit_code"
    assert set(decision.detail["shared_candidates"]) == {
        "91610103MABOHNNLOU",
        "91610103MAB0HNNLOU",
    }


def test_critical_identifier_asymmetric_cross_source_conflict_requires_review() -> None:
    original = _identifier_document(
        "91610103MABOHNNLOU",
        [("ppocrv5", "91610103MABOHNNLOU"), ("ppstructure", "91610103MAB0HNNLOU")],
    )
    compare = _identifier_document(
        "91610103MAB0HNNLOU",
        [("ppocrv5", "91610103MAB0HNNLOU"), ("ppstructure", "91610103MAB0HNNLOU")],
    )
    diff = _identifier_diff()

    result = DiffQualityProcessor().process([diff], original_document=original, compare_document=compare)

    assert len(result.diffs) == 1
    assert result.diffs[0].diff_type == "MODIFY"
    assert result.diffs[0].quality_status == "NEEDS_REVIEW"
    assert result.diffs[0].match_score_details["change_decision"] == "NEEDS_REVIEW"
    assert "CRITICAL_IDENTIFIER_SOURCE_CONFLICT" in result.diffs[0].review_flags


def test_stable_critical_identifier_sources_confirm_real_o_zero_change() -> None:
    original = _identifier_document(
        "91610103MABOHNNLOU",
        [("ppocrv5", "91610103MABOHNNLOU"), ("ppstructure", "91610103MABOHNNLOU")],
    )
    compare = _identifier_document(
        "91610103MAB0HNNLOU",
        [("ppocrv5", "91610103MAB0HNNLOU"), ("ppstructure", "91610103MAB0HNNLOU")],
    )
    diff = _identifier_diff()

    result = DiffQualityProcessor().process([diff], original_document=original, compare_document=compare)

    assert len(result.diffs) == 1
    assert result.diffs[0].quality_status == "NORMAL"
    assert result.diffs[0].match_score_details["change_decision"] == "CONFIRMED"
    assert not TextEvidenceEquivalence().compare("MABO", "MAB0").equivalent
    assert any(
        item.action == "confirmed_diff" and item.detail["reason"] == "CRITICAL_IDENTIFIER_DIFFERENCE_CONFIRMED"
        for item in result.decisions
    )


def test_diff_quality_suppresses_seal_occluded_party_label_noise() -> None:
    diff = DiffItem(
        diff_id="D003",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：封面信息",
        original_text="甲 東方",
        compare_text="甲方",
        review_flags=["PAGE_UNRELIABLE", "SEAL_OR_SIGNATURE_RISK"],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert result.decisions[-1].detail["reason"] == "seal_occluded_party_label_noise"


def test_diff_quality_uses_native_page_text_for_missing_table_extraction(tmp_path: Path) -> None:
    original_path = tmp_path / "original.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=200)
    page.insert_text((30, 90), "Project Content Quantity Unit Manufacturer Total", fontsize=10)
    pdf.save(original_path)
    pdf.close()
    original_document = Document(
        filename=original_path.name,
        path=str(original_path),
        page_count=1,
        pages=[Page(page_no=1, width=300, height=200, blocks=[])],
    )
    diff = DiffItem(
        diff_id="D004",
        diff_type="ADD",
        source_type="table",
        title="表格字段：标的物",
        compare_text="Project | Content | Quantity Unit | Manufacturer | Total",
        compare_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=20, y0=70, x1=290, y1=110))],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert result.decisions[-1].detail["reason"] == "cross_source_table_text_confirmed"


def test_diff_quality_uses_spatial_native_phrase_for_corrupted_table_label(tmp_path: Path) -> None:
    original_path = tmp_path / "original-label.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=200)
    page.insert_text((70, 90), "A B", fontsize=12)
    pdf.save(original_path)
    pdf.close()
    original_document = Document(
        filename=original_path.name,
        path=str(original_path),
        page_count=1,
        pages=[Page(page_no=1, width=300, height=200, blocks=[])],
    )
    diff = DiffItem(
        diff_id="D005",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：封面信息",
        original_text="AXB",
        compare_text="AB",
        original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=60, y0=70, x1=110, y1=105))],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert result.decisions[-1].detail["reason"] == "cross_source_table_text_confirmed"


def test_diff_quality_keeps_reliable_percent_unit_change_confirmed(tmp_path: Path) -> None:
    original = _percent_document(tmp_path / "original.pdf", text="5%作为违约金", loop_count=2)
    compare = _percent_document(tmp_path / "compare.pdf", text="5‰作为违约金", loop_count=3)

    result = DiffQualityProcessor().process(
        [_percent_diff()],
        original_document=original,
        compare_document=compare,
    )

    assert len(result.diffs) == 1
    assert result.diffs[0].quality_status == "NORMAL"
    assert "CRITICAL_VALUE_CHANGE" in result.diffs[0].review_flags
    assert any(
        item.action == "confirmed_diff"
        and item.detail["reason"] == "PERCENT_UNIT_VISUAL_DIFFERENCE_CONFIRMED"
        for item in result.decisions
    )


def _percent_diff() -> DiffItem:
    bbox = BBox(x0=60, y0=26, x1=150, y1=56)
    return DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_text="5%作为违约金",
        compare_text="5‰作为违约金",
        original_snippet="5%",
        compare_snippet="5‰",
        review_flags=["CRITICAL_FIELD_CHANGE", "CRITICAL_FIELD_PERCENT_RATE_CHANGE"],
        original_evidence=[EvidenceBox(page_no=1, bbox=bbox, text="5%作为违约金")],
        compare_evidence=[EvidenceBox(page_no=1, bbox=bbox, text="5‰作为违约金")],
    )


def _percent_document(path: Path, *, text: str, loop_count: int) -> Document:
    pdf = fitz.open()
    page = pdf.new_page(width=180, height=90)
    page.draw_circle((74, 34), 4, color=(0, 0, 0), width=1.4)
    page.draw_circle((84, 48), 4, color=(0, 0, 0), width=1.4)
    if loop_count == 3:
        page.draw_circle((74, 48), 4, color=(0, 0, 0), width=1.4)
    page.draw_line((77, 51), (82, 31), color=(0, 0, 0), width=1.4)
    pdf.save(path)
    pdf.close()
    block = TextBlock(
        block_id="p1_b1",
        page_no=1,
        text=text,
        bbox=BBox(x0=60, y0=26, x1=150, y1=56),
        source="ppocrv5_layout_matched",
        char_boxes=[
            CharBox(char="5", page_no=1, bbox=BBox(x0=60, y0=28, x1=67, y1=54), text_index=0),
            CharBox(char=text[1], page_no=1, bbox=BBox(x0=70, y0=28, x1=87, y1=54), text_index=1),
            CharBox(char="作", page_no=1, bbox=BBox(x0=91, y0=28, x1=104, y1=54), text_index=2),
        ],
    )
    return Document(
        filename=path.name,
        path=str(path),
        page_count=1,
        pages=[Page(page_no=1, width=180, height=90, blocks=[block])],
    )


def _identifier_diff() -> DiffItem:
    bbox = BBox(x0=100, y0=500, x1=260, y1=525)
    return DiffItem(
        diff_id="D012",
        diff_type="MODIFY",
        source_type="signing_region",
        title="甲方 · 税号",
        original_text="91610103MABOHNNLOU",
        compare_text="91610103MAB0HNNLOU",
        original_snippet="91610103MABOHNNLOU",
        compare_snippet="91610103MAB0HNNLOU",
        match_score_details={"field_key": "credit_code"},
        review_flags=["CRITICAL_VALUE_CHANGE", "PAGE_UNRELIABLE"],
        original_evidence=[EvidenceBox(page_no=1, bbox=bbox, text="91610103MABOHNNLOU")],
        compare_evidence=[EvidenceBox(page_no=1, bbox=bbox, text="91610103MAB0HNNLOU")],
    )


def _identifier_document(primary: str, candidates: list[tuple[str, str]]) -> Document:
    bbox = BBox(x0=60, y0=490, x1=270, y1=530)
    return Document(
        filename="identifier.pdf",
        path="",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="tax-id",
                        page_no=1,
                        text=f"税号：{primary}",
                        bbox=bbox,
                        source="ppocrv5_layout_matched",
                        source_evidence=[
                            {
                                "source": source,
                                "raw_text": f"税号：{value}",
                                "page_no": 1,
                                "bbox": bbox,
                                "confidence": 0.98,
                            }
                            for source, value in candidates
                        ],
                    )
                ],
            )
        ],
    )
