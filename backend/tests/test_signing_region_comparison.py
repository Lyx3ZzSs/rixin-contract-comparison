import pytest

from app.models import BBox, DiffItem, EvidenceBox
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.coverage import SigningRegionCoverageBuilder
from app.services.signing_region.diff_builder import SigningRegionDiffBuilder
from app.services.signing_region.matcher import SigningRegionMatcher
from app.services.signing_region.models import SigningElement, SigningElementType, SigningRegion, SigningRegionRole


def _region(
    region_id: str,
    text: str,
    *,
    page_no: int = 1,
    x0: float = 60,
    element_type: SigningElementType = SigningElementType.SEAL,
) -> SigningRegion:
    return SigningRegion(
        region_id=region_id,
        page_no=page_no,
        bbox=BBox(x0=x0, y0=650, x1=x0 + 160, y1=780),
        region_role=SigningRegionRole.PARTY_A,
        confidence=0.9,
        confidence_reasons=["test"],
        elements=[
            SigningElement(
                element_id=f"{region_id}-{element_type.value}",
                element_type=element_type,
                page_no=page_no,
                bbox=BBox(x0=x0 + 20, y0=680, x1=x0 + 120, y1=760),
                text=text,
                confidence=0.9,
                source="layout",
            )
        ],
    )


def _field(
    element_id: str,
    role: str,
    field_key: str,
    label: str,
    text: str,
    *,
    x0: float = 80,
    value_bbox: BBox | None = None,
    field_prefix: str | None = None,
) -> SigningElement:
    return SigningElement(
        element_id=element_id,
        element_type=SigningElementType.FIELD,
        page_no=1,
        bbox=BBox(x0=x0, y0=680, x1=x0 + 120, y1=710),
        text=text,
        confidence=0.9,
        source="inferred",
        raw_ref={
            "party_role": role,
            "field_key": field_key,
            "field_label": label,
            **({"field_prefix": field_prefix} if field_prefix is not None else {}),
            **({"value_bbox": value_bbox.model_dump()} if value_bbox is not None else {}),
        },
    )


def _party(element_id: str, role: str, text: str, *, bbox: BBox) -> SigningElement:
    return SigningElement(
        element_id=element_id,
        element_type=SigningElementType.PARTY_FIELD,
        page_no=1,
        bbox=bbox,
        text=text,
        confidence=0.9,
        source="inferred",
        raw_ref={"party_role": role},
    )


def _visual_signature(element_id: str, role: str, *, visual_hash: str = "") -> SigningElement:
    return SigningElement(
        element_id=element_id,
        element_type=SigningElementType.SIGNATURE,
        page_no=1,
        bbox=BBox(x0=90, y0=710, x1=180, y1=760),
        text="signature",
        confidence=0.9,
        source="visual_model",
        visual_hash=visual_hash,
        raw_ref={"party_role": role},
    )


def _visual_region(element_id: str, *, x0: float = 50) -> SigningElement:
    return SigningElement(
        element_id=element_id,
        element_type=SigningElementType.VISUAL_AREA,
        page_no=1,
        bbox=BBox(x0=x0, y0=620, x1=x0 + 470, y1=780),
        confidence=1.0,
        source="visual_fingerprint",
        visual_hash=f"hash-{element_id}",
    )


def test_comparator_outputs_one_diff_per_signing_field() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.extend(
        [
            _field("o-address", "乙方", "address", "地址", "北京市海淀区"),
            _field("o-account", "乙方", "account", "账号", "6222 0011"),
        ]
    )
    compare.elements.extend(
        [
            _field("c-address", "乙方", "address", "地址", "北京市朝阳区"),
            _field("c-account", "乙方", "account", "账号", "6222-0099"),
        ]
    )

    comparison = SigningRegionComparator().compare(original, compare)
    diffs = SigningRegionDiffBuilder().build_diffs([comparison])

    assert [diff.title for diff in diffs] == ["乙方 · 账号", "乙方 · 地址"]
    assert all(diff.section_type == "signature:field" for diff in diffs)
    assert all(len(diff.original_evidence) == 1 for diff in diffs)


def test_comparator_suppresses_party_name_ocr_gaps_when_party_references_and_peer_fields_agree() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.confidence_reasons.append("two_column_layout")
    compare.confidence_reasons.append("two_column_layout")
    original.elements.extend(
        [
            _field("o-name-a", "甲方", "party_name", "单位名称", "定边县瑞能新能源科技有限", x0=80),
            _field("o-address-a", "甲方", "address", "单位地址", "陕西省榆林市", x0=80),
            _field("o-phone-a", "甲方", "phone", "电话", "029-61825538", x0=80),
            _field("o-address-b", "乙方", "address", "单位地址", "北京市海淀区", x0=320),
            _field("o-phone-b", "乙方", "phone", "电话", "010-83458100", x0=320),
        ]
    )
    compare.elements.extend(
        [
            _field("c-address-a", "甲方", "address", "单位地址", "陕西省榆林市", x0=80),
            _field("c-phone-a", "甲方", "phone", "电话", "029-61825538", x0=80),
            _field("c-name-b", "乙方", "party_name", "单位名称", "国能日新科技股份有限公司", x0=320),
            _field("c-address-b", "乙方", "address", "单位地址", "北京市海淀区", x0=320),
            _field("c-phone-b", "乙方", "phone", "电话", "010-83458100", x0=320),
        ]
    )

    comparison = SigningRegionComparator().compare(
        original,
        compare,
        original_party_references={
            "甲方": "定边县瑞能新能源科技有限公司",
            "乙方": "国能日新科技股份有限公司",
        },
        compare_party_references={
            "甲方": "定边县瑞能新能源科技有限公司",
            "乙方": "国能日新科技股份有限公司",
        },
    )

    assert not [change for change in comparison.field_changes if change["field_key"] == "party_name"]


def test_diff_builder_keeps_multiline_field_evidence_as_separate_boxes() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original_field = _field(
        "o-address",
        "乙方",
        "address",
        "地址",
        "北京市海淀区西三旗建材城中路27号1幢2层227号",
    )
    compare_field = _field("c-address", "乙方", "address", "地址", "北京市路27号1幢")
    original_boxes = [BBox(x0=310, y0=180, x1=530, y1=194), BBox(x0=310, y0=210, x1=460, y1=226)]
    compare_boxes = [BBox(x0=300, y0=181, x1=385, y1=198), BBox(x0=300, y0=211, x1=385, y1=228)]
    original_field.raw_ref["value_bboxes"] = [bbox.model_dump() for bbox in original_boxes]
    compare_field.raw_ref["value_bboxes"] = [bbox.model_dump() for bbox in compare_boxes]
    original.elements.append(original_field)
    compare.elements.append(compare_field)

    diff = SigningRegionDiffBuilder().build_diffs(
        [SigningRegionComparator().compare(original, compare, match_confidence=0.9)]
    )[0]

    assert [evidence.bbox for evidence in diff.original_evidence] == original_boxes
    assert [evidence.bbox for evidence in diff.compare_evidence] == compare_boxes


def test_comparator_reports_truncated_signing_field_labels_per_party() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.extend(
        [
            _field("o-a", "甲方", "legal_representative", "法人代表或授权委托人", "", x0=90),
            _field("o-b", "乙方", "legal_representative", "法人代表或授权委托人", "", x0=330),
        ]
    )
    compare.elements.extend(
        [
            _field("c-a", "甲方", "legal_representative", "法人", "", x0=90),
            _field("c-b", "乙方", "legal_representative", "法人代表或", "", x0=330),
        ]
    )

    comparison = SigningRegionComparator().compare(original, compare)
    diffs = SigningRegionDiffBuilder().build_diffs([comparison])

    assert [(diff.title, diff.original_text, diff.compare_text) for diff in diffs] == [
        ("乙方 · 法人代表或授权委托人", "法人代表或授权委托人", "法人代表或"),
        ("甲方 · 法人代表或授权委托人", "法人代表或授权委托人", "法人"),
    ]
    assert diffs[0].compare_evidence[0].bbox.x0 == 330
    assert diffs[1].compare_evidence[0].bbox.x0 == 90


def test_diff_builder_keeps_field_highlight_separate_from_recognition_outline() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.extend(
        [
            _field("o-date", "乙方", "date", "日期", ""),
            _visual_region("o-visual"),
        ]
    )
    compare.elements.extend(
        [
            _field(
                "c-date",
                "乙方",
                "date",
                "日期",
                "2026.",
                value_bbox=BBox(x0=135, y0=680, x1=180, y1=710),
            ),
            _visual_region("c-visual"),
        ]
    )

    comparison = SigningRegionComparator().compare(original, compare)
    diff = SigningRegionDiffBuilder().build_diffs([comparison])[0]

    assert diff.diff_type == "ADD"
    assert diff.original_text == ""
    assert diff.compare_text == "2026."
    assert diff.original_evidence == []
    assert [evidence.method for evidence in diff.compare_evidence] == ["signing_region_element"]
    assert diff.compare_evidence[0].bbox == BBox(x0=135, y0=680, x1=180, y1=710)


def test_comparator_classifies_cleared_field_value_as_delete() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.append(_field("o-date", "乙方", "date", "日期", "2026."))
    compare.elements.append(_field("c-date", "乙方", "date", "日期", ""))

    comparison = SigningRegionComparator().compare(original, compare)
    diff = SigningRegionDiffBuilder().build_diffs([comparison])[0]

    assert diff.diff_type == "DELETE"
    assert diff.original_text == "2026."
    assert diff.compare_text == ""


def test_comparator_preserves_complete_text_when_structured_fields_are_deleted() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.extend(
        [
            _field("o-address", "乙方", "address", "地址", "北京市", field_prefix="地址："),
            _field(
                "o-legal",
                "乙方",
                "legal_representative",
                "法人代表或授权委托人",
                "",
                field_prefix="法人代表或授权委托人：",
            ),
            _field("o-postal", "乙方", "postal_code", "邮编", "100096", field_prefix="邮编："),
            _field("o-signature", "乙方", "signature", "(签字)", "", field_prefix="(签字)"),
        ]
    )
    compare.elements.append(_field("c-address", "乙方", "address", "地址", "北京市", field_prefix="地址："))

    diffs = SigningRegionDiffBuilder().build_diffs([SigningRegionComparator().compare(original, compare)])
    deleted_text = {diff.title: diff.original_text for diff in diffs}

    assert deleted_text == {
        "乙方 · (签字)": "(签字)",
        "乙方 · 法人代表或授权委托人": "法人代表或授权委托人：",
        "乙方 · 邮编": "邮编：100096",
    }
    assert all(diff.diff_type == "DELETE" for diff in diffs)
    assert all(len(diff.original_evidence) == 1 for diff in diffs)
    assert all(not diff.compare_evidence for diff in diffs)


def test_comparator_ignores_signature_image_identity_when_both_slots_are_signed() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    for region, prefix, visual_hash in [(original, "o", "alice"), (compare, "c", "bob")]:
        region.elements.extend(
            [
                _field(f"{prefix}-slot", "甲方", "authorized_representative", "授权代表（签字）", ""),
                _visual_signature(f"{prefix}-signature", "甲方", visual_hash=visual_hash),
            ]
        )

    comparison = SigningRegionComparator().compare(original, compare)

    assert comparison.signature_changes == []
    assert comparison.diff_type is None


def test_comparator_detects_signature_slot_presence_change() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.append(_field("o-slot", "甲方", "authorized_representative", "授权代表（签字）", ""))
    compare.elements.extend(
        [
            _field("c-slot", "甲方", "authorized_representative", "授权代表（签字）", ""),
            _visual_signature("c-signature", "甲方"),
        ]
    )

    comparison = SigningRegionComparator().compare(original, compare)
    diff = SigningRegionDiffBuilder().build_diffs([comparison])[0]

    assert comparison.signature_changes[0]["type"] == "ADD"
    assert diff.section_type == "signature:signature"
    assert diff.readable_change.endswith("未签 → 已签")
    assert diff.compare_evidence[0].bbox == BBox(x0=90, y0=710, x1=180, y1=760)


def test_comparator_assigns_visual_signature_to_nearest_signature_field() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    for region in (original, compare):
        region.elements.extend(
            [
                _field("legal", "甲方", "legal_representative", "法定代表人或授权代表签字", ""),
                _field("date", "甲方", "signature", "签字日期", ""),
            ]
        )
        region.elements[-2].bbox = BBox(x0=80, y0=188, x1=230, y1=209)
        region.elements[-1].bbox = BBox(x0=80, y0=513, x1=230, y1=535)
    compare.elements.append(
        SigningElement(
            element_id="visual-signature",
            element_type=SigningElementType.SIGNATURE,
            page_no=1,
            bbox=BBox(x0=132, y0=227, x1=195, y1=281),
            text="signature",
            confidence=0.68,
            source="visual_model",
        )
    )

    comparison = SigningRegionComparator().compare(original, compare)
    diff = SigningRegionDiffBuilder().build_diffs([comparison])[0]

    assert diff.title == "甲方 · 法定代表人或授权代表签字"
    assert diff.compare_evidence[0].bbox == BBox(x0=132, y0=227, x1=195, y1=281)


def test_comparator_detects_deleted_signature_labels_without_treating_them_as_handwriting() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.extend(
        [
            _field("o-a-sign", "甲方", "signature", "(签字)", "", x0=180),
            _field("o-b-sign", "乙方", "signature", "(签字)", "", x0=397),
        ]
    )
    compare.elements.extend(
        [
            _field("c-a-date", "甲方", "date", "日期", ""),
            _field("c-b-date", "乙方", "date", "日期", ""),
        ]
    )

    comparison = SigningRegionComparator().compare(original, compare)
    diffs = SigningRegionDiffBuilder().build_diffs([comparison])

    signature_diffs = [diff for diff in diffs if diff.title.endswith("(签字)")]
    assert [(diff.title, diff.diff_type, diff.original_text, diff.compare_text) for diff in signature_diffs] == [
        ("乙方 · (签字)", "DELETE", "(签字)", ""),
        ("甲方 · (签字)", "DELETE", "(签字)", ""),
    ]
    assert comparison.signature_changes == []
    assert all(diff.original_evidence[0].method == "signing_region_element" for diff in signature_diffs)


def test_comparator_collapses_whole_column_deletion() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.extend(
        [
            _field("o-a", "甲方", "address", "地址", "北京"),
            _field("o-b-address", "乙方", "address", "地址", "上海"),
            _field("o-b-phone", "乙方", "phone", "电话", "021-1234"),
        ]
    )
    compare.elements.append(_field("c-a", "甲方", "address", "地址", "北京"))

    comparison = SigningRegionComparator().compare(original, compare)
    diffs = SigningRegionDiffBuilder().build_diffs([comparison])

    assert len(comparison.column_changes) == 1
    assert comparison.field_changes == []
    assert len(diffs) == 1
    assert diffs[0].section_type == "signature:column"
    assert diffs[0].readable_change == "乙方 · 签署栏：乙方签署栏 → 空白"


def test_comparator_matches_repeated_unknown_fields_by_value_before_position() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.extend(
        [
            _field("o-1", "unknown", "postal_code", "邮政编码", "813000"),
            _field("o-2", "unknown", "postal_code", "邮政编码", "100096"),
        ]
    )
    compare.elements.extend(
        [
            _field("c-1", "unknown", "postal_code", "邮政编码", "100096"),
            _field("c-2", "unknown", "postal_code", "邮政编码", "813000"),
        ]
    )

    comparison = SigningRegionComparator().compare(original, compare)

    assert comparison.field_changes == []


def test_comparator_ignore_seals_keeps_other_signing_fields() -> None:
    original = _region("O1", "A章")
    compare = _region("C1", "B章")
    original.elements.append(_field("o-address", "甲方", "address", "地址", "北京"))
    compare.elements.append(_field("c-address", "甲方", "address", "地址", "上海"))

    comparison = SigningRegionComparator().compare(original, compare, ignore_seals=True)
    diffs = SigningRegionDiffBuilder().build_diffs([comparison])

    assert comparison.seal_changes == []
    assert len(diffs) == 1
    assert diffs[0].section_type == "signature:field"


def test_comparator_ignore_seals_suppresses_unmatched_seal_only_region() -> None:
    comparison = SigningRegionComparator().compare(None, _region("C1", "合同专用章"), ignore_seals=True)

    assert comparison.diff_type is None
    assert SigningRegionDiffBuilder().build_diffs([comparison]) == []


def test_matcher_pairs_regions_by_page_and_role() -> None:
    original = [_region("O1", "A公司")]
    compare = [_region("C1", "A公司")]

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs == [(original[0], compare[0], 1.0)]


def test_matcher_does_not_pair_same_page_zero_overlap_regions() -> None:
    original = [_region("O1", "A公司", x0=60)]
    compare = [_region("C1", "A公司", x0=360)]

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs == [(original[0], None, 0.0), (None, compare[0], 0.0)]


def test_matcher_pairs_same_role_regions_after_layout_rearrangement() -> None:
    original = [_region("O1", "甲方（盖章）： 日期：", x0=60)]
    compare = [_region("C1", "甲方（盖章）： 日期：", x0=360)]

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs == [(original[0], compare[0], 0.75)]


def test_matcher_pairs_same_role_regions_after_signing_page_moves() -> None:
    original = [_region("O1", "甲方（盖章）：授权代表（签字）：日期：", page_no=10, x0=60)]
    compare = [_region("C1", "甲方（盖章）：授权代表（签字）：日期：", page_no=15, x0=360)]

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs[0][0] is original[0]
    assert pairs[0][1] is compare[0]
    assert pairs[0][2] == 0.5625


def test_low_confidence_signing_match_is_exposed_on_field_diff() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.append(_field("o-address", "甲方", "address", "地址", "北京"))
    compare.elements.append(_field("c-address", "甲方", "address", "地址", "上海"))

    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.55)
    diff = SigningRegionDiffBuilder().build_diffs([comparison])[0]

    assert diff.match_confidence == 0.55
    assert "SIGNING_MATCH_LOW_CONFIDENCE" in diff.review_flags


def test_matcher_does_not_pair_adjacent_page_zero_overlap_regions() -> None:
    original = [_region("O1", "A公司", page_no=1, x0=60)]
    compare = [_region("C1", "A公司", page_no=2, x0=360)]

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs == [(original[0], None, 0.0), (None, compare[0], 0.0)]


def test_matcher_pairs_adjacent_page_signing_blocks_by_text_role_even_when_position_moves() -> None:
    original = [_region("O1", "甲方：A 乙方：B (盖章) (签字) 日期：", page_no=10, x0=60)]
    compare = [_region("C1", "甲方：A 乙方：B (盖章) (签字) 日期：2026.", page_no=11, x0=60)]
    original[0].signing_block_id = "SB-10-1"
    compare[0].signing_block_id = "SB-11-1"
    original[0].bbox = BBox(x0=60, y0=620, x1=520, y1=740)
    compare[0].bbox = BBox(x0=60, y0=60, x1=520, y1=180)

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs[0][0] is original[0]
    assert pairs[0][1] is compare[0]
    assert pairs[0][2] >= 0.55


def test_matcher_pairs_high_confidence_adjacent_signing_blocks_when_compare_ocr_misses_some_labels() -> None:
    original = [_region("O1", "甲方：A 乙方：B (盖章) 法人代表或授权委托人： (签字) 日期：", page_no=10)]
    compare = [_region("C1", "甲方：A 乙方：B (盖章) 法人代表或 日期：2026.", page_no=11)]
    original[0].region_role = SigningRegionRole.BOTH_PARTIES
    compare[0].region_role = SigningRegionRole.BOTH_PARTIES
    original[0].confidence = 0.85
    compare[0].confidence = 0.75
    original[0].bbox = BBox(x0=48, y0=602, x1=501, y1=744)
    compare[0].bbox = BBox(x0=57, y0=44, x1=524, y1=191)

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs == [(original[0], compare[0], 0.55)]


def test_matcher_pairs_adjacent_business_tail_signing_region_with_full_signature_page() -> None:
    original = [
        _region(
            "O1",
            "签署页\n甲方：国家电网有限公司华北分部 乙方：国能日新科技股份有限公司\n"
            "(盖章)\n法定代表人（负责人）或授权代表（签字）：\n签订日期：\n"
            "地址：北京市西城区广安门内大街482号 地址：北京市海淀区建材城中路27号\n"
            "联系人：环加飞 联系人：刘玉良\n电话：010-83582793 电话：18811089109\n"
            "传真：010-83582600 传真：010-83458100\n"
            "Email: huan.jiafei@nc.sgcc.com.cn Email: yuliang.liu@sprixin.com\n"
            "统一社会信用代码：91110000053621038D 统一社会信用代码：911101086723891430",
            page_no=25,
        )
    ]
    compare = [
        _region(
            "C1",
            "地址：北京市西城区广安门内大街 地址：北京市海淀区建材城中路\n"
            "482号 27号金隅智造工场N6\n"
            "联系人：环加飞 联系人：刘玉良\n"
            "电话：010-83582793 电话：18811089109\n"
            "传真：010-83582600 传真：010-83458100\n"
            "Email: huan.jiafei@nc.sgcc.com.cn Email: yuliang.liu@sprixin.com\n"
            "统一社会信用代码：91110000053621038D 统一社会信用代码：911101086723891430",
            page_no=24,
        )
    ]
    original[0].region_role = SigningRegionRole.BOTH_PARTIES
    compare[0].region_role = SigningRegionRole.UNKNOWN
    original[0].confidence = 1.0
    compare[0].confidence = 0.7
    original[0].bbox = BBox(x0=70, y0=70, x1=520, y1=501)
    compare[0].bbox = BBox(x0=73, y0=281, x1=525, y1=495)

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs[0][0] is original[0]
    assert pairs[0][1] is compare[0]
    assert pairs[0][2] >= 0.55


def test_matcher_does_not_use_adjacent_page_fallback_for_low_confidence_visual_candidates() -> None:
    original = [_region("O1", "甲方：A 乙方：B (盖章) 法人代表或授权委托人： (签字) 日期：", page_no=10)]
    compare = [_region("C1", "甲方：A 乙方：B (盖章) 法人代表或 日期：2026.", page_no=11)]
    original[0].region_role = SigningRegionRole.BOTH_PARTIES
    compare[0].region_role = SigningRegionRole.BOTH_PARTIES
    original[0].confidence = 0.55
    compare[0].confidence = 0.55
    original[0].bbox = BBox(x0=48, y0=602, x1=501, y1=744)
    compare[0].bbox = BBox(x0=57, y0=44, x1=524, y1=191)

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs == [(original[0], None, 0.0), (None, compare[0], 0.0)]


def test_comparator_detects_seal_text_change() -> None:
    comparison = SigningRegionComparator().compare(_region("O1", "A公司"), _region("C1", "B公司"), match_confidence=0.9)

    assert comparison.diff_type == "MODIFY"
    assert comparison.seal_changes[0]["original_text"] == "A公司"
    assert comparison.seal_changes[0]["compare_text"] == "B公司"
    assert "SIGNING_SEAL_CHANGE" in comparison.review_flags


def test_comparator_reports_seal_moved_between_two_signing_columns() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    for region in (original, compare):
        region.confidence_reasons = ["two_column_layout"]
        region.elements.extend(
            [
                _field(f"{region.region_id}-phone-a", "甲方", "phone", "电话", "", x0=60),
                _field(f"{region.region_id}-phone-b", "乙方", "phone", "电话", "", x0=320),
            ]
        )
    original.elements.append(
        SigningElement(
            element_id="o-seal",
            element_type=SigningElementType.SEAL,
            page_no=1,
            bbox=BBox(x0=120, y0=650, x1=240, y1=750),
            text="seal",
            confidence=0.8,
            source="visual_model",
        )
    )
    compare.elements.append(
        SigningElement(
            element_id="c-seal",
            element_type=SigningElementType.SEAL,
            page_no=1,
            bbox=BBox(x0=380, y0=650, x1=480, y1=750),
            text="seal",
            confidence=0.8,
            source="visual_model",
        )
    )
    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.9)

    assert [(change["type"], change["party_role"]) for change in comparison.seal_changes] == [
        ("DELETE", "甲方"),
        ("ADD", "乙方"),
    ]


def test_comparator_reports_added_seal_without_original_region_evidence() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare.elements.append(
        SigningElement(
            element_id="C1-seal",
            element_type=SigningElementType.SEAL,
            page_no=1,
            bbox=BBox(x0=120, y0=700, x1=200, y1=760),
            text="seal",
            confidence=0.8,
            source="visual_model",
        )
    )

    diff = SigningRegionDiffBuilder().build_diffs(
        [SigningRegionComparator().compare(original, compare, match_confidence=0.9)]
    )[0]

    assert diff.diff_type == "ADD"
    assert diff.original_evidence == []
    assert [evidence.text for evidence in diff.compare_evidence] == ["seal"]


@pytest.mark.parametrize(
    ("element_type", "changes_attr", "flag", "original_text", "compare_text"),
    [
        (
            SigningElementType.DATE_FIELD,
            "date_changes",
            "SIGNING_DATE_CHANGE",
            "签订日期：2026年5月1日",
            "签订日期：2026年5月2日",
        ),
        (SigningElementType.LABEL, "label_changes", "SIGNING_LABEL_CHANGE", "甲方（盖章）：", "乙方（盖章）："),
        (SigningElementType.SIGNING_TABLE, "table_changes", "SIGNING_TABLE_CHANGE", "授权代表：张三", "授权代表：李四"),
    ],
)
def test_comparator_detects_non_seal_element_text_changes(
    element_type: SigningElementType,
    changes_attr: str,
    flag: str,
    original_text: str,
    compare_text: str,
) -> None:
    comparison = SigningRegionComparator().compare(
        _region("O1", original_text, element_type=element_type),
        _region("C1", compare_text, element_type=element_type),
        match_confidence=0.9,
    )

    changes = getattr(comparison, changes_attr)
    assert comparison.diff_type == "MODIFY"
    assert changes[0]["original_text"] == original_text
    assert changes[0]["compare_text"] == compare_text
    assert flag in comparison.review_flags


def test_comparator_no_change_does_not_build_diff() -> None:
    comparison = SigningRegionComparator().compare(_region("O1", "A公司"), _region("C1", "A公司"), match_confidence=1.0)

    diffs = SigningRegionDiffBuilder().build_diffs([comparison])

    assert comparison.diff_type is None
    assert diffs == []


def test_party_name_change_is_exposed_as_focused_signing_modify() -> None:
    original = _region(
        "O1",
        "甲方：国能长源随州发电有限公司\n乙方：国能日新科技股份有限公司\n随县分公司",
        page_no=53,
        element_type=SigningElementType.SIGNING_TABLE,
    )
    compare = _region(
        "C1",
        "甲方：国能长源随州发电有限公司\n乙方：国能日新科技股份有限公司",
        page_no=53,
        element_type=SigningElementType.SIGNING_TABLE,
    )
    original.elements.append(
        SigningElement(
            element_id="O1-party-a",
            element_type=SigningElementType.PARTY_FIELD,
            page_no=53,
            bbox=BBox(x0=71, y0=86, x1=287, y1=137),
            text="甲方：国能长源随州发电有限公司随县分公司",
            confidence=0.9,
            source="inferred",
            raw_ref={"party_role": "甲方", "source_block_ids": ["party_a", "party_a_cont"]},
        )
    )
    compare.elements.append(
        SigningElement(
            element_id="C1-party-a",
            element_type=SigningElementType.PARTY_FIELD,
            page_no=53,
            bbox=BBox(x0=89, y0=101, x1=287, y1=128),
            text="甲方：国能长源随州发电有限公司",
            confidence=0.9,
            source="inferred",
            raw_ref={"party_role": "甲方", "source_block_ids": ["party_a"]},
        )
    )

    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.98)
    diff = SigningRegionDiffBuilder().build_diffs([comparison])[0]

    assert comparison.party_changes == [
        {
            "type": "MODIFY",
            "element_type": "party_field",
            "party_role": "甲方",
            "change_scope": "field",
            "original_text": "甲方：国能长源随州发电有限公司随县分公司",
            "compare_text": "甲方：国能长源随州发电有限公司",
        }
    ]
    assert diff.diff_type == "MODIFY"
    assert diff.original_snippet == "甲方：国能长源随州发电有限公司随县分公司"
    assert diff.compare_snippet == "甲方：国能长源随州发电有限公司"
    assert diff.original_evidence[0].text == diff.original_snippet
    assert diff.original_evidence[0].bbox == BBox(x0=71, y0=86, x1=287, y1=137)
    assert diff.compare_evidence[0].text == diff.compare_snippet
    assert len(diff.original_evidence) == 1
    assert len(diff.compare_evidence) == 1
    assert diff.original_evidence[0].method == "signing_region_element"
    assert "甲方：国能长源随州发电有限公司随县分公司 → 甲方：国能长源随州发电有限公司" in diff.readable_change
    assert "SIGNING_PARTY_CHANGE" in diff.review_flags
    assert "CRITICAL_VALUE_CHANGE" in diff.review_flags


def test_deleted_party_with_stamp_suffix_keeps_all_text_and_evidence_segments() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    for region, prefix in ((original, "o"), (compare, "c")):
        region.elements.extend(
            [
                _field(f"{prefix}-phone-a", "甲方", "phone", "电话", "010-1", x0=90),
                _field(f"{prefix}-phone-b", "乙方", "phone", "电话", "010-2", x0=310),
            ]
        )
    party_bbox = BBox(x0=310, y0=134, x1=505, y1=198)
    name_bbox = BBox(x0=310, y0=134, x1=505, y1=150)
    tail_bbox = BBox(x0=310, y0=158, x1=327, y1=175)
    stamp_bbox = BBox(x0=313, y0=181, x1=364, y1=198)
    original.elements.append(
        SigningElement(
            element_id="o-party-b",
            element_type=SigningElementType.PARTY_FIELD,
            page_no=1,
            bbox=party_bbox,
            text="乙方：国能日新科技股份有限公司(盖章)",
            confidence=0.9,
            source="inferred",
            raw_ref={
                "party_role": "乙方",
                "field_bboxes": [bbox.model_dump() for bbox in (name_bbox, tail_bbox, stamp_bbox)],
                "field_segments": ["乙方：国能日新科技股份有限公", "司", "(盖章)"],
            },
        )
    )

    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.9)
    diff = next(diff for diff in SigningRegionDiffBuilder().build_diffs([comparison]) if "签署主体" in diff.title)

    assert diff.diff_type == "DELETE"
    assert diff.original_text == "乙方：国能日新科技股份有限公司(盖章)"
    assert [evidence.text for evidence in diff.original_evidence] == [
        "乙方：国能日新科技股份有限公",
        "司",
        "(盖章)",
    ]
    assert [evidence.bbox for evidence in diff.original_evidence] == [name_bbox, tail_bbox, stamp_bbox]


def test_party_ocr_conflict_under_seal_is_reconciled_by_document_references() -> None:
    original = _region(
        "O1",
        "甲方：江苏东大金智信息系统有限公司",
        element_type=SigningElementType.SIGNING_TABLE,
    )
    compare = _region(
        "C1",
        "甲方：江苏东达金智信息系统有限公司",
        element_type=SigningElementType.SIGNING_TABLE,
    )
    for region, text in [
        (original, "甲方：江苏东大金智信息系统有限公司"),
        (compare, "甲方：江苏东达金智信息系统有限公司"),
    ]:
        region.elements.append(
            SigningElement(
                element_id=f"{region.region_id}-party",
                element_type=SigningElementType.PARTY_FIELD,
                page_no=region.page_no,
                bbox=region.bbox,
                text=text,
                confidence=0.95,
                source="inferred",
                raw_ref={"party_role": "甲方"},
            )
        )
    compare.elements.append(
        SigningElement(
            element_id="C1-seal",
            element_type=SigningElementType.SEAL,
            page_no=compare.page_no,
            bbox=compare.bbox,
            text="seal",
            confidence=0.8,
            source="visual_model",
        )
    )

    comparator = SigningRegionComparator()
    comparator.reconcile_occluded_party_ocr(
        original,
        compare,
        original_party_references={"甲方": "江苏东大金智信息系统有限公司"},
        compare_party_references={"甲方": "江苏东大金智信息系统有限公司"},
    )
    comparison = comparator.compare(
        original,
        compare,
        match_confidence=0.9,
        original_party_references={"甲方": "江苏东大金智信息系统有限公司"},
        compare_party_references={"甲方": "江苏东大金智信息系统有限公司"},
    )

    assert comparison.party_changes == []
    assert comparison.table_changes == []
    assert "东达" not in compare.elements[0].text
    assert "东大" in compare.elements[0].text
    assert "SIGNING_PARTY_CHANGE" not in comparison.review_flags
    assert "SIGNING_SEAL_CHANGE" in comparison.review_flags


def test_party_single_character_change_without_seal_remains_critical() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    for region, text in [
        (original, "甲方：江苏东大金智信息系统有限公司"),
        (compare, "甲方：江苏东达金智信息系统有限公司"),
    ]:
        region.elements.append(
            SigningElement(
                element_id=f"{region.region_id}-party",
                element_type=SigningElementType.PARTY_FIELD,
                page_no=region.page_no,
                bbox=region.bbox,
                text=text,
                confidence=0.95,
                source="inferred",
                raw_ref={"party_role": "甲方"},
            )
        )

    comparison = SigningRegionComparator().compare(
        original,
        compare,
        match_confidence=0.9,
        original_party_references={"甲方": "江苏东大金智信息系统有限公司"},
        compare_party_references={"甲方": "江苏东大金智信息系统有限公司"},
    )

    assert len(comparison.party_changes) == 1
    assert "SIGNING_PARTY_CHANGE" in comparison.review_flags


def test_party_prefixes_truncated_by_seal_match_shared_document_reference() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    for region, text in [
        (original, "甲方：【南京国电南自电网自动化有限公"),
        (compare, "甲方：【南"),
    ]:
        region.elements.append(
            SigningElement(
                element_id=f"{region.region_id}-party",
                element_type=SigningElementType.PARTY_FIELD,
                page_no=region.page_no,
                bbox=region.bbox,
                text=text,
                confidence=0.55,
                source="inferred",
                raw_ref={"party_role": "甲方"},
            )
        )
    compare.elements.append(
        SigningElement(
            element_id="C1-seal",
            element_type=SigningElementType.SEAL,
            page_no=1,
            bbox=compare.bbox,
            text="seal",
            confidence=0.8,
            source="visual_model",
        )
    )
    compare.elements.append(_field("c-address", "甲方", "address", "地址", "南京39号"))

    comparator = SigningRegionComparator()
    comparator.reconcile_occluded_party_ocr(
        original,
        compare,
        original_party_references={"甲方": "南京国电南自电网自动化有限公司"},
        compare_party_references={"甲方": "南京国电南自电网自动化有限公司"},
    )
    comparison = comparator.compare(
        original,
        compare,
        match_confidence=0.9,
        original_party_references={"甲方": "南京国电南自电网自动化有限公司"},
        compare_party_references={"甲方": "南京国电南自电网自动化有限公司"},
    )

    assert comparison.party_changes == []
    assert next(element for element in compare.elements if element.element_id == "c-address").text == "南京39号"


def test_party_references_accept_bracketed_cover_names() -> None:
    from app.models import Document, Page, TextBlock

    document = Document(
        filename="cover.pdf",
        path="cover.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="party",
                        page_no=1,
                        text="甲方：【南京国电南自电网自动化有限公司】",
                        bbox=BBox(x0=60, y0=180, x1=300, y1=200),
                    )
                ],
            )
        ],
    )

    assert SigningRegionComparator.party_references(document) == {"甲方": "南京国电南自电网自动化有限公司"}


def test_comparator_ignores_missing_party_label_when_sealed_two_column_fields_remain() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare.confidence_reasons = ["two_column_layout"]
    original.elements.append(
        SigningElement(
            element_id="o-party-b",
            element_type=SigningElementType.PARTY_FIELD,
            page_no=1,
            bbox=original.bbox,
            text="乙方：【国能日新科技股份有限公司】（盖章）",
            confidence=0.9,
            source="inferred",
            raw_ref={"party_role": "乙方"},
        )
    )
    for region in (original, compare):
        region.elements.extend(
            [
                _field(f"{region.region_id}-phone-a", "甲方", "phone", "电话", ""),
                _field(f"{region.region_id}-phone-b", "乙方", "phone", "电话", "", x0=320),
            ]
        )
    compare.elements.append(
        SigningElement(
            element_id="c-seal",
            element_type=SigningElementType.SEAL,
            page_no=1,
            bbox=compare.bbox,
            text="seal",
            confidence=0.8,
            source="visual_model",
        )
    )

    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.9)

    assert comparison.party_changes == []


def test_comparator_ignores_seal_truncated_address_with_same_prefix_and_number() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.append(_field("o-address", "甲方", "address", "地址", "南京市江宁经济技术开发区水阁路39号"))
    compare.elements.extend(
        [
            _field("c-address", "甲方", "address", "地址", "南京39号"),
            SigningElement(
                element_id="C1-seal",
                element_type=SigningElementType.SEAL,
                page_no=1,
                bbox=compare.bbox,
                text="seal",
                confidence=0.8,
                source="visual_model",
            ),
        ]
    )

    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.9)

    assert comparison.field_changes == []


def test_comparator_ignores_blank_repeated_field_missing_from_one_ocr_column() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.extend(
        [
            _field("o-auth-b", "乙方", "authorized_representative", "授权代表签字", ""),
            _field("o-phone-a", "甲方", "phone", "电话", "025-12345678"),
        ]
    )
    compare.elements.extend(
        [
            _field("c-auth-a", "甲方", "authorized_representative", "授权代表签", ""),
            _field("c-phone-b", "乙方", "phone", "电话", "025-12345678"),
        ]
    )

    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.9)

    assert not [change for change in comparison.field_changes if change["field_key"] == "authorized_representative"]


def test_comparator_reports_residual_party_as_modify_and_missing_labels_as_delete() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    for region in (original, compare):
        region.confidence_reasons = ["two_column_layout"]
        region.elements.extend(
            [
                _party(
                    f"{region.region_id}-party-a",
                    "甲方",
                    "甲方：【南京国电南自电网自动化有限公司】（盖章）",
                    bbox=BBox(x0=60, y0=650, x1=280, y1=675),
                ),
                _field(f"{region.region_id}-auth-a", "甲方", "authorized_representative", "授权代表签", ""),
                _field(f"{region.region_id}-credit-a", "甲方", "credit_code", "纳税人识别", ""),
                _field(f"{region.region_id}-address-a", "甲方", "address", "地址", ""),
                _field(f"{region.region_id}-phone-b", "乙方", "phone", "电话", "", x0=320),
            ]
        )
    original.elements.extend(
        [
            _party(
                "o-party-b",
                "乙方",
                "乙方：【国能日新科技股份有限公司】（盖章）",
                bbox=BBox(x0=300, y0=650, x1=540, y1=675),
            ),
            _field(
                "o-auth-b",
                "乙方",
                "authorized_representative",
                "授权代表签字",
                "",
                x0=320,
                field_prefix="授权代表签字：",
            ),
            _field(
                "o-credit-b",
                "乙方",
                "credit_code",
                "纳税人识别号",
                "",
                x0=320,
                field_prefix="纳税人识别号：",
            ),
            _field("o-address-b", "乙方", "address", "地址", "", x0=320, field_prefix="地址："),
            SigningElement(
                element_id="o-seal-a",
                element_type=SigningElementType.SEAL,
                page_no=1,
                bbox=BBox(x0=195, y0=680, x1=260, y1=735),
                text="seal",
                confidence=0.8,
                source="visual_model",
                raw_ref={"party_role": "甲方"},
            ),
        ]
    )
    compare.elements.append(
        SigningElement(
            element_id="c-seal-b",
            element_type=SigningElementType.SEAL,
            page_no=1,
            bbox=BBox(x0=350, y0=720, x1=430, y1=760),
            text="seal",
            confidence=0.8,
            source="visual_model",
            raw_ref={"party_role": "乙方"},
        )
    )
    compare.elements.append(
        SigningElement(
            element_id="c-party-residual-b",
            element_type=SigningElementType.LABEL,
            page_no=1,
            bbox=BBox(x0=500, y0=650, x1=540, y1=675),
            text="盖章）",
            confidence=0.9,
            source="inferred",
            raw_ref={"party_role": "乙方", "residual_kind": "party_suffix"},
        )
    )

    comparison = SigningRegionComparator().compare(
        original,
        compare,
        match_confidence=0.9,
        original_party_references={"乙方": "国能日新科技股份有限公司"},
        compare_party_references={"乙方": "国能日新科技股份有限公司"},
        ignore_seals=True,
    )
    diffs = SigningRegionDiffBuilder().build_diffs([comparison])

    deleted = {(diff.title, diff.original_text) for diff in diffs if diff.diff_type == "DELETE"}
    assert not [change for change in comparison.field_changes if change["party_role"] == "甲方"]
    assert deleted == {
        ("乙方 · 授权代表签字", "授权代表签字："),
        ("乙方 · 纳税人识别号", "纳税人识别号："),
        ("乙方 · 地址", "地址："),
    }
    party_diff = next(diff for diff in diffs if diff.title == "乙方 · 签署主体变化")
    assert party_diff.diff_type == "MODIFY"
    assert party_diff.compare_text == ""
    assert party_diff.readable_change.endswith("→ 主体缺失（仅残留格式文本“盖章）”）")
    assert len(party_diff.compare_evidence) == 1
    assert party_diff.compare_evidence[0].bbox == BBox(x0=500, y0=650, x1=540, y1=675)
    assert party_diff.compare_evidence[0].text == "盖章）"
    assert party_diff.compare_evidence[0].highlight_type == "MODIFY"


def test_party_and_visual_changes_keep_focused_field_and_full_signing_region_evidence() -> None:
    original = _region(
        "O1",
        "甲方：国能长源随州发电有限公司\n乙方：国能日新科技股份有限公司\n随县分公司",
        page_no=53,
        element_type=SigningElementType.SIGNING_TABLE,
    )
    compare = _region(
        "C1",
        "甲方：国能长源随州发电有限公司\n乙方：国能日新科技股份有限公司",
        page_no=53,
        element_type=SigningElementType.SIGNING_TABLE,
    )
    original.elements.append(
        SigningElement(
            element_id="O1-party-a",
            element_type=SigningElementType.PARTY_FIELD,
            page_no=53,
            bbox=BBox(x0=71, y0=86, x1=287, y1=137),
            text="甲方：国能长源随州发电有限公司随县分公司",
            confidence=0.9,
            source="inferred",
            raw_ref={"party_role": "甲方"},
        )
    )
    compare.elements.extend(
        [
            SigningElement(
                element_id="C1-party-a",
                element_type=SigningElementType.PARTY_FIELD,
                page_no=53,
                bbox=BBox(x0=89, y0=101, x1=287, y1=128),
                text="甲方：国能长源随州发电有限公司",
                confidence=0.9,
                source="inferred",
                raw_ref={"party_role": "甲方"},
            ),
            SigningElement(
                element_id="C1-seal",
                element_type=SigningElementType.SEAL,
                page_no=53,
                bbox=BBox(x0=120, y0=120, x1=230, y1=230),
                text="合同专用章",
                confidence=0.9,
                source="visual_model",
            ),
        ]
    )

    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.98)
    diffs = SigningRegionDiffBuilder().build_diffs([comparison])
    party_diff = next(diff for diff in diffs if diff.section_type == "signature:party")
    seal_diff = next(diff for diff in diffs if diff.section_type == "signature:seal")

    assert "SIGNING_PARTY_CHANGE" in party_diff.review_flags
    assert "SIGNING_SEAL_CHANGE" in seal_diff.review_flags
    assert party_diff.original_evidence[0].text == "甲方：国能长源随州发电有限公司随县分公司"
    assert party_diff.original_evidence[0].bbox == BBox(x0=71, y0=86, x1=287, y1=137)
    assert party_diff.compare_evidence[0].text == "甲方：国能长源随州发电有限公司"


def test_diff_builder_add_uses_compare_side_only() -> None:
    comparison = SigningRegionComparator().compare(None, _region("C1", "B公司"), match_confidence=0.0)

    diffs = SigningRegionDiffBuilder().build_diffs([comparison])

    assert len(diffs) == 1
    assert diffs[0].diff_type == "ADD"
    assert diffs[0].original_evidence == []
    assert len(diffs[0].compare_evidence) == 1
    assert diffs[0].original_change_ranges == []
    assert len(diffs[0].compare_change_ranges) == 1


def test_diff_builder_delete_uses_original_side_only() -> None:
    comparison = SigningRegionComparator().compare(_region("O1", "A公司"), None, match_confidence=0.0)

    diffs = SigningRegionDiffBuilder().build_diffs([comparison])

    assert len(diffs) == 1
    assert diffs[0].diff_type == "DELETE"
    assert len(diffs[0].original_evidence) == 1
    assert diffs[0].compare_evidence == []
    assert len(diffs[0].original_change_ranges) == 1
    assert diffs[0].compare_change_ranges == []


def test_diff_builder_outputs_signing_region_diff() -> None:
    comparison = SigningRegionComparator().compare(_region("O1", "A公司"), _region("C1", "B公司"), match_confidence=0.9)

    diffs = SigningRegionDiffBuilder().build_diffs([comparison], start_index=3)

    assert len(diffs) == 1
    assert diffs[0].diff_id == "D003"
    assert diffs[0].source_type == "signing_region"
    assert "A公司" in diffs[0].original_text
    assert "B公司" in diffs[0].compare_text
    assert diffs[0].original_evidence[0].method == "signing_region_element"


def test_diff_builder_title_shows_signing_region_page_for_same_page_match() -> None:
    comparison = SigningRegionComparator().compare(
        _region("O1", "日期：", page_no=10),
        _region("C1", "日期：2026.", page_no=10),
        match_confidence=0.8,
    )

    diff = SigningRegionDiffBuilder().build_diffs([comparison], start_index=1)[0]

    assert diff.title == "印章文字变化"


def test_diff_builder_title_shows_original_and_compare_pages_for_cross_page_match() -> None:
    comparison = SigningRegionComparator().compare(
        _region("O1", "日期：", page_no=10),
        _region("C1", "日期：2026.", page_no=11),
        match_confidence=0.8,
    )

    diff = SigningRegionDiffBuilder().build_diffs([comparison], start_index=1)[0]

    assert diff.title == "印章文字变化"


def test_coverage_hides_overlapping_seal_diff() -> None:
    region_diff = SigningRegionDiffBuilder().build_diffs(
        [
            SigningRegionComparator().compare(
                _region("O1", "A公司"),
                _region("C1", "B公司"),
                match_confidence=0.9,
            )
        ],
        start_index=10,
    )[0]
    legacy = DiffItem(
        diff_id="D002",
        diff_type="ADD",
        source_type="seal",
        title="印章区域（第1页）",
        compare_text="B公司",
        compare_evidence=[
            EvidenceBox(page_no=1, bbox=BBox(x0=90, y0=680, x1=180, y1=760), method="seal_region", text="B公司")
        ],
    )

    result = SigningRegionCoverageBuilder().build([region_diff], [legacy])

    assert result.covered_diff_ids == {"D002"}
    assert result.entries[0].signing_region_diff_id == "D010"


def test_coverage_hides_broad_legacy_seal_region_containing_focused_signing_seal() -> None:
    signing_diff = DiffItem(
        diff_id="D012",
        diff_type="ADD",
        source_type="signing_region",
        title="甲方 · 印章",
        review_flags=["SIGNING_SEAL_CHANGE"],
        compare_evidence=[
            EvidenceBox(
                page_no=13,
                bbox=BBox(x0=111, y0=197, x1=187, y1=241),
                method="signing_region_element",
                text="seal",
            )
        ],
    )
    legacy = DiffItem(
        diff_id="D009",
        diff_type="ADD",
        source_type="seal",
        title="印章区域（第13页）",
        compare_text="单位名 公司 法定代表人或授",
        compare_evidence=[
            EvidenceBox(
                page_no=13,
                bbox=BBox(x0=80, y0=114, x1=237, y1=297),
                method="seal_region",
                text="单位名 公司 法定代表人或授",
            )
        ],
    )

    result = SigningRegionCoverageBuilder().build([signing_diff], [legacy])

    assert result.covered_diff_ids == {"D009"}


def test_coverage_hides_overlapping_signing_contact_delete_described_only_by_snippet() -> None:
    region_diff = SigningRegionDiffBuilder().build_diffs(
        [
            SigningRegionComparator().compare(
                _region("O1", "签署表原文", element_type=SigningElementType.SIGNING_TABLE),
                _region("C1", "签署表被遮挡", element_type=SigningElementType.SIGNING_TABLE),
                match_confidence=0.9,
            )
        ],
        start_index=12,
    )[0]
    legacy = DiffItem(
        diff_id="D006",
        diff_type="DELETE",
        source_type="table",
        title="表格字段：联系人",
        original_snippet="27号1幢2层227号 | 创业孵化基地1号楼0814室",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=90, y0=680, x1=180, y1=710),
                method="table_cell",
                text="创业孵化基地1号楼0814室",
            )
        ],
    )

    result = SigningRegionCoverageBuilder().build([region_diff], [legacy])

    assert result.covered_diff_ids == {"D006"}
    assert result.entries[0].reasons["D006"] == "overlaps_confirmed_signing_region"


def test_coverage_hides_overlapping_table_diff_for_confirmed_signing_field_delete() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.append(_field("o-name", "unknown", "party_name", "单位名称（章）", "国能日新科技股份有限公司"))
    region_diff = SigningRegionDiffBuilder().build_diffs(
        [SigningRegionComparator().compare(original, compare, match_confidence=0.9)],
        start_index=12,
    )[0]
    legacy = DiffItem(
        diff_id="D005",
        diff_type="DELETE",
        source_type="table",
        title="表格字段：联系人",
        original_text="单位名称（章）：国能日新科技股份有限公司",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=80, y0=680, x1=220, y1=710),
                method="table_cell",
                text="单位名称（章）：国能日新科技股份有限公司",
            )
        ],
    )

    result = SigningRegionCoverageBuilder().build([region_diff], [legacy])

    assert "SIGNING_FIELD_CHANGE" in region_diff.review_flags
    assert result.covered_diff_ids == {"D005"}


def test_signing_field_diff_does_not_cover_overlapping_seal_diff() -> None:
    original = _region("O1", "", element_type=SigningElementType.SIGNING_TABLE)
    compare = _region("C1", "", element_type=SigningElementType.SIGNING_TABLE)
    original.elements.append(_field("o-name", "unknown", "party_name", "单位名称", "甲公司"))
    region_diff = SigningRegionDiffBuilder().build_diffs(
        [SigningRegionComparator().compare(original, compare, match_confidence=0.9)],
        start_index=12,
    )[0]
    seal = DiffItem(
        diff_id="D011",
        diff_type="ADD",
        source_type="seal",
        title="印章区域（第1页）",
        compare_text="合同专用章",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=90, y0=680, x1=180, y1=760),
                method="seal_region",
                text="合同专用章",
            )
        ],
    )

    result = SigningRegionCoverageBuilder().build([region_diff], [seal])

    assert result.covered_diff_ids == set()


def test_coverage_does_not_hide_same_bbox_seal_diff_on_different_page() -> None:
    region_diff = SigningRegionDiffBuilder().build_diffs(
        [
            SigningRegionComparator().compare(
                _region("O1", "A公司", page_no=1),
                _region("C1", "B公司", page_no=1),
                match_confidence=0.9,
            )
        ],
        start_index=10,
    )[0]
    legacy = DiffItem(
        diff_id="D002",
        diff_type="ADD",
        source_type="seal",
        title="印章区域（第2页）",
        compare_text="B公司",
        compare_evidence=[
            EvidenceBox(page_no=2, bbox=BBox(x0=60, y0=650, x1=220, y1=780), method="seal_region", text="B公司")
        ],
    )

    result = SigningRegionCoverageBuilder().build([region_diff], [legacy])

    assert result.covered_diff_ids == set()
    assert result.entries == []
