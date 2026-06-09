from __future__ import annotations

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.signature_compare import SignatureComparator


def _block(
    block_id: str,
    text: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    role: str = "signature_field",
    block_type: str = "text",
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=1,
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        block_role=role,
        block_type=block_type,
    )


def _document(blocks: list[TextBlock]) -> Document:
    return Document(
        filename="contract.pdf",
        path="contract.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=blocks)],
    )


def _char_boxes(text: str, x0: float, y0: float, *, char_width: float = 8.0, height: float = 12.0) -> list[CharBox]:
    return [
        CharBox(
            char=char,
            page_no=1,
            bbox=BBox(x0=x0 + index * char_width, y0=y0, x1=x0 + (index + 1) * char_width, y1=y0 + height),
            text_index=index,
        )
        for index, char in enumerate(text)
    ]


def test_signature_compare_reports_added_right_date_and_keeps_equal_account() -> None:
    original = _document(
        [
            _block("o_left_account", "账号：532658192135", 65, 450, 185, 470),
            _block("o_right_account", "账号：", 300, 450, 350, 470),
            _block("o_left_date", "日期：", 65, 500, 105, 520),
            _block("o_left_date_value", "2026.4.17", 120, 500, 190, 520),
            _block("o_right_date", "日期：", 300, 500, 340, 520),
        ]
    )
    compare = _document(
        [
            _block("c_left_account", "账号：532658192135", 65, 450, 185, 470),
            _block("c_right_account", "账号：", 300, 450, 350, 470),
            _block("c_left_date", "日期：", 65, 500, 105, 520),
            _block("c_left_date_value", "2026.4.17", 120, 500, 190, 520),
            _block("c_right_date_value", "2021.4.17", 315, 490, 390, 520),
            _block("c_right_date", "日期：", 300, 500, 340, 520),
        ]
    )

    diffs = SignatureComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].source_type == "signature"
    assert diffs[0].diff_type == "ADD"
    assert diffs[0].title == "签署栏字段：右栏日期"
    assert diffs[0].compare_snippet == "2021.4.17"
    assert [evidence.method for evidence in diffs[0].compare_evidence] == ["signature_estimated_text"]
    assert "532658192135" not in diffs[0].readable_change


def test_signature_compare_reports_unlabeled_handwritten_signature_but_ignores_seal_fragments() -> None:
    original = _document(
        [
            _block("o_right_party", "乙方：【国能日新科技股份有限公司】（盖章）", 300, 340, 520, 360),
            _block("o_noise", "站股", 420, 365, 455, 380),
        ]
    )
    compare = _document(
        [
            _block("c_right_party", "乙方：【国能日新科技股份有限公司】（盖章）", 300, 340, 520, 360),
            _block("c_noise", "站股", 420, 365, 455, 380),
            _block("c_name", "刘万社", 500, 365, 560, 395),
        ]
    )

    diffs = SignatureComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].source_type == "signature"
    assert diffs[0].diff_type == "ADD"
    assert diffs[0].title == "签署栏字段：右栏未标注签署文本"
    assert diffs[0].compare_snippet == "刘万社"
    assert "站股" not in diffs[0].readable_change


def test_signature_compare_matches_ocr_address_variant_as_modify() -> None:
    original = _document(
        [
            _block("o_address", "地址：南京市江宁经济技术开发区水阁路", 65, 430, 280, 450),
        ]
    )
    compare = _document(
        [
            _block("c_address", "地址：南京江宁经济技术开发区水阁路", 65, 432, 270, 452),
        ]
    )

    diffs = SignatureComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "MODIFY"
    assert diffs[0].match_method == "signature_label_position_similarity"
    assert diffs[0].match_score is not None
    assert diffs[0].match_score_details["text_score"] >= 90


def test_signature_compare_matches_missing_label_by_position_and_similarity() -> None:
    original = _document(
        [
            _block("o_address", "地址：南京市江宁经济技术开发区水阁路", 65, 430, 280, 450),
        ]
    )
    compare = _document(
        [
            _block("c_address_value", "南京江宁经济技术开发区水阁路", 65, 432, 270, 452),
        ]
    )

    diffs = SignatureComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "MODIFY"
    assert diffs[0].match_method == "signature_weighted_similarity"
    assert diffs[0].match_score is not None
    assert diffs[0].match_score_details["label_score"] == 58


def test_signature_compare_keeps_account_token_change_as_modify() -> None:
    original = _document(
        [
            _block("o_account", "账号：532658192135", 65, 450, 185, 470),
        ]
    )
    compare = _document(
        [
            _block("c_account", "账号：532658192136", 65, 450, 185, 470),
        ]
    )

    diffs = SignatureComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "MODIFY"
    assert diffs[0].match_score_details["business_token_mismatch"] == 1.0
    assert "532658192135" in diffs[0].original_text
    assert "532658192136" in diffs[0].compare_text


def test_signature_compare_uses_char_evidence_for_changed_signature_value() -> None:
    original_text = "账号：532658192135"
    compare_text = "账号：532658192136"
    original_block = _block("o_account", original_text, 65, 450, 185, 470)
    compare_block = _block("c_account", compare_text, 65, 450, 185, 470)
    original_block.char_boxes = _char_boxes(original_text, 65, 450)
    compare_block.char_boxes = _char_boxes(compare_text, 65, 450)

    diffs = SignatureComparator().build_diffs(
        _document([original_block]),
        _document([compare_block]),
    )

    assert len(diffs) == 1
    assert {evidence.method for evidence in diffs[0].original_evidence} == {"signature_char_exact"}
    assert {evidence.method for evidence in diffs[0].compare_evidence} == {"signature_char_exact"}
    assert diffs[0].original_evidence[0].bbox.x0 > original_block.bbox.x0
    assert diffs[0].original_evidence[0].bbox.x1 <= original_block.bbox.x1


def test_signature_compare_estimates_value_evidence_without_char_boxes() -> None:
    compare_block = _block("c_date", "日期：2026.4.17", 300, 500, 420, 520)

    diffs = SignatureComparator().build_diffs(_document([]), _document([compare_block]))

    assert len(diffs) == 1
    evidence = diffs[0].compare_evidence[0]
    assert evidence.method == "signature_estimated_text"
    assert evidence.text == "2026.4.17"
    assert evidence.bbox.x0 > compare_block.bbox.x0
    assert evidence.bbox.x1 <= compare_block.bbox.x1
    assert evidence.bbox.x1 - evidence.bbox.x0 < compare_block.bbox.x1 - compare_block.bbox.x0


def test_signature_compare_suppresses_missing_empty_label() -> None:
    """Empty unmatched labels (e.g., '日期：' with no value) are suppressed as noise."""
    original = _document(
        [
            _block("o_date", "日期：", 65, 500, 105, 520),
        ]
    )
    compare = _document([])

    diffs = SignatureComparator().build_diffs(original, compare)

    assert diffs == []


def test_signature_compare_ignores_equal_empty_labels() -> None:
    original = _document(
        [
            _block("o_date", "日期：", 65, 500, 105, 520),
        ]
    )
    compare = _document(
        [
            _block("c_date", "日期：", 65, 500, 105, 520),
        ]
    )

    diffs = SignatureComparator().build_diffs(original, compare)

    assert diffs == []


def test_signature_compare_keeps_value_change_when_empty_label_gets_value() -> None:
    original = _document(
        [
            _block("o_date", "日期：", 300, 500, 340, 520),
        ]
    )
    compare = _document(
        [
            _block("c_date", "日期：2026.4.17", 300, 500, 390, 520),
        ]
    )

    diffs = SignatureComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "ADD"
    assert diffs[0].title == "签署栏字段：右栏日期"
    assert diffs[0].compare_text == "2026.4.17"


def test_signature_compare_matches_partial_signature_page_without_whole_field_delete() -> None:
    original = _document(
        [
            _block("o_party_a", "甲方：南京瑞尚电力科技有限公司", 63, 117, 272, 131),
            _block("o_party_b", "乙方：国能日新科技股份有限公司", 303, 117, 512, 131),
            _block("o_address", "地址：江苏省南京市栖霞区八卦洲街地址：北京市海淀区西三旗建材城中", 61, 179, 532, 194),
            _block("o_left_tail", "道鹏岛路254号悦福大厦12-0044", 62, 211, 269, 225),
            _block("o_right_tail", "路27号1幢2层227号", 308, 210, 456, 226),
            _block("o_person_a", "法人代表或授权委托人：", 62, 242, 205, 256),
            _block("o_zip_a", "邮编：", 59, 301, 98, 322),
            _block("o_zip_b", "邮编：100096", 302, 303, 388, 320),
            _block("o_left_date", "日期：", 60, 332, 97, 353),
            _block("o_right_date", "日期：", 301, 333, 340, 353),
        ]
    )
    compare = _document(
        [
            _block("c_party_a", "甲方：南京瑞尚电力科技有限公司", 62, 122, 266, 137),
            _block("c_party_b", "乙方：国能日新科技股份右限公司", 295, 119, 502, 138),
            _block("c_address", "地址：江苏省南京市栖霞区八卦洲街地址：北京市", 59, 180, 384, 198),
            _block("c_left_tail", "道鹏岛路254号悦福大厦12-0044", 60, 212, 264, 227),
            _block("c_right_tail", "路27号1幢：", 300, 211, 385, 228),
        ]
    )

    diffs = SignatureComparator().build_diffs(original, compare)

    deleted_titles = [diff.title for diff in diffs if diff.diff_type == "DELETE"]
    modified_titles = [diff.title for diff in diffs if diff.diff_type == "MODIFY"]

    assert "签署栏字段：左栏甲方" not in deleted_titles
    assert "签署栏字段：右栏乙方" not in deleted_titles
    assert "签署栏字段：右栏乙方" in modified_titles
    assert "签署栏字段：左栏地址" not in deleted_titles
    assert "签署栏字段：右栏地址" in modified_titles
    assert "签署栏字段：左栏法人代表" in deleted_titles
    assert "签署栏字段：左栏法人代表" in deleted_titles
    assert "签署栏字段：右栏邮编" in deleted_titles


def test_signature_compare_splits_double_column_address_and_attaches_tails_by_column() -> None:
    document = _document(
        [
            _block("party_a", "甲方：南京瑞尚电力科技有限公司", 62, 122, 266, 137),
            _block("party_b", "乙方：国能日新科技股份有限公司", 295, 119, 502, 138),
            _block("address", "地址：江苏省南京市栖霞区八卦洲街地址：北京市", 59, 180, 384, 198),
            _block("left_tail", "道鹏岛路254号悦福大厦12-0044", 60, 212, 264, 227),
            _block("right_tail", "路27号1幢：", 300, 211, 385, 228),
        ]
    )

    fields = SignatureComparator()._collect_fields(document)
    by_label = {
        (field.column, field.label.split("#", maxsplit=1)[0]): field.text
        for field in fields
    }

    assert by_label[("left", "地址")] == "江苏省南京市栖霞区八卦洲街 道鹏岛路254号悦福大厦12-0044"
    assert by_label[("right", "地址")] == "北京市 路27号1幢"


def test_signature_compare_splits_merged_party_labels_into_left_right() -> None:
    """Merged block '甲方：X乙方：Y' should split into left 甲方 and right 乙方."""
    document = _document(
        [
            _block("merged", "甲方：江苏东大金智信息系统有限公司乙方：国能日新科技股份有限公司", 65, 340, 520, 360),
        ]
    )

    fields = SignatureComparator()._collect_fields(document)
    by_label = {
        (field.column, field.label.split("#", maxsplit=1)[0]): field.text
        for field in fields
    }

    assert ("left", "甲方") in by_label
    assert ("right", "乙方") in by_label
    assert "江苏东大金智信息系统有限公司" in by_label[("left", "甲方")]
    assert "国能日新科技股份有限公司" in by_label[("right", "乙方")]


def test_signature_compare_splits_noise_prefix_multi_label_block() -> None:
    """Block starting with noise followed by field labels should extract all fields."""
    document = _document(
        [
            _block(
                "giant",
                "司】（盖章\n账号：532658192135\n地址：南京市江宁经济技术开发区水阁路\n电话：025-69833061\n日期：2026.4.17",
                65, 450, 285, 700,
            ),
        ]
    )

    fields = SignatureComparator()._collect_fields(document)
    by_label = {
        field.label.split("#", maxsplit=1)[0]: field.text
        for field in fields
    }

    assert "账号" in by_label
    assert "532658192135" in by_label["账号"]
    assert "地址" in by_label
    assert "南京市江宁经济技术开发区水阁路" in by_label["地址"]
    assert "电话" in by_label
    assert "025-69833061" in by_label["电话"]
    assert "日期" in by_label
    assert "2026.4.17" in by_label["日期"]


def test_signature_compare_multi_label_block_single_label_falls_through() -> None:
    """Block with a single label at start should use normal single-label parsing."""
    document = _document(
        [
            _block("single", "账号：532658192135", 65, 450, 185, 470),
        ]
    )

    fields = SignatureComparator()._collect_fields(document)
    assert len(fields) == 1
    assert fields[0].text == "532658192135"
