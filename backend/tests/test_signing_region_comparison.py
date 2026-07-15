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


def test_matcher_does_not_pair_same_page_far_regions_by_text_structure_only() -> None:
    original = [_region("O1", "甲方（盖章）： 日期：", x0=60)]
    compare = [_region("C1", "甲方（盖章）： 日期：", x0=360)]

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs == [(original[0], None, 0.0), (None, compare[0], 0.0)]


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
    original = [_region(
        "O1",
        "签署页\n甲方：国家电网有限公司华北分部 乙方：国能日新科技股份有限公司\n"
        "(盖章)\n法定代表人（负责人）或授权代表（签字）：\n签订日期：\n"
        "地址：北京市西城区广安门内大街482号 地址：北京市海淀区建材城中路27号\n"
        "联系人：环加飞 联系人：刘玉良\n电话：010-83582793 电话：18811089109\n"
        "传真：010-83582600 传真：010-83458100\n"
        "Email: huan.jiafei@nc.sgcc.com.cn Email: yuliang.liu@sprixin.com\n"
        "统一社会信用代码：91110000053621038D 统一社会信用代码：911101086723891430",
        page_no=25,
    )]
    compare = [_region(
        "C1",
        "地址：北京市西城区广安门内大街 地址：北京市海淀区建材城中路\n"
        "482号 27号金隅智造工场N6\n"
        "联系人：环加飞 联系人：刘玉良\n"
        "电话：010-83582793 电话：18811089109\n"
        "传真：010-83582600 传真：010-83458100\n"
        "Email: huan.jiafei@nc.sgcc.com.cn Email: yuliang.liu@sprixin.com\n"
        "统一社会信用代码：91110000053621038D 统一社会信用代码：911101086723891430",
        page_no=24,
    )]
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


@pytest.mark.parametrize(
    ("element_type", "changes_attr", "flag", "original_text", "compare_text"),
    [
        (SigningElementType.DATE_FIELD, "date_changes", "SIGNING_DATE_CHANGE", "签订日期：2026年5月1日", "签订日期：2026年5月2日"),
        (SigningElementType.SIGNATURE, "signature_changes", "SIGNING_SIGNATURE_CHANGE", "张三", "李四"),
        (SigningElementType.LABEL, "label_changes", "SIGNING_LABEL_CHANGE", "甲方（盖章）：", "乙方（盖章）："),
        (SigningElementType.SIGNING_TABLE, "table_changes", "SIGNING_TABLE_CHANGE", "授权代表：张三", "授权代表：李四"),
        (SigningElementType.VISUAL_AREA, "visual_changes", "SIGNING_VISUAL_CHANGE", "视觉区域A", "视觉区域B"),
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
    assert diff.original_evidence[1].method == "signing_region_visual"
    assert diff.original_evidence[1].highlight_type == "MODIFY"
    assert diff.compare_evidence[1].method == "signing_region_visual"
    assert diff.compare_evidence[1].highlight_type == "MODIFY"
    assert "签署主体变化：甲方：国能长源随州发电有限公司随县分公司 → 甲方：国能长源随州发电有限公司" in diff.readable_change
    assert "SIGNING_PARTY_CHANGE" in diff.review_flags
    assert "CRITICAL_VALUE_CHANGE" in diff.review_flags


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
    diff = SigningRegionDiffBuilder().build_diffs([comparison])[0]

    assert "SIGNING_PARTY_CHANGE" in diff.review_flags
    assert "SIGNING_SEAL_CHANGE" in diff.review_flags
    assert diff.original_evidence[0].text == "甲方：国能长源随州发电有限公司随县分公司"
    assert diff.original_evidence[0].bbox == BBox(x0=71, y0=86, x1=287, y1=137)
    assert diff.original_evidence[1].method == "signing_region_visual"
    assert diff.original_evidence[1].highlight_type == "MODIFY"
    assert diff.original_evidence[1].bbox == original.bbox
    assert diff.compare_evidence[0].text == "甲方：国能长源随州发电有限公司"
    assert diff.compare_evidence[1].method == "signing_region_visual"
    assert diff.compare_evidence[1].highlight_type == "MODIFY"
    assert diff.compare_evidence[1].bbox == compare.bbox


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
    assert diffs[0].original_evidence[0].method == "signing_region"


def test_diff_builder_title_shows_signing_region_page_for_same_page_match() -> None:
    comparison = SigningRegionComparator().compare(
        _region("O1", "日期：", page_no=10),
        _region("C1", "日期：2026.", page_no=10),
        match_confidence=0.8,
    )

    diff = SigningRegionDiffBuilder().build_diffs([comparison], start_index=1)[0]

    assert diff.title == "签署区（第10页）"


def test_diff_builder_title_shows_original_and_compare_pages_for_cross_page_match() -> None:
    comparison = SigningRegionComparator().compare(
        _region("O1", "日期：", page_no=10),
        _region("C1", "日期：2026.", page_no=11),
        match_confidence=0.8,
    )

    diff = SigningRegionDiffBuilder().build_diffs([comparison], start_index=1)[0]

    assert diff.title == "签署区（原第10页 / 新第11页）"


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
