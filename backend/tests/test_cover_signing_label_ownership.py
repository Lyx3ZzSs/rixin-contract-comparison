"""Regression for seal OCR leaking into fields and hiding printed seal labels."""

import pytest

from app.models import BBox, Document, Page, TextBlock
from app.services.cover.facade import CoverMetadataComparator
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.extractor import SigningRegionExtractor
from app.services.signing_region.models import SigningBlock


def block(ident, text, box, kind="text", confidence=0.99):
    return TextBlock(
        block_id=ident, page_no=1, text=text, block_type=kind,
        bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
        confidence=confidence, source="PP-OCRv6",
    )


def document(blocks):
    return Document(filename="sample.pdf", path="", page_count=1,
                    pages=[Page(page_no=1, width=595, height=817, blocks=blocks)])


@pytest.mark.parametrize("kind", ["seal", "stamp"])
@pytest.mark.parametrize("next_label", [False, True])
def test_cover_blank_place_does_not_claim_stamp_text(kind, next_label):
    label = block("place", "签订地点", (115, 553, 177, 574), "table")
    original = [label]
    if next_label:
        original.append(block("date", "签订日期", (115, 593, 177, 614), "table"))
    # Even a stamp on the same row cannot become the field's value.
    noise = block("edge", "北工型", (567, 553, 595, 574), kind, 0.3586)
    diffs = CoverMetadataComparator().build_diffs(document(original), document([*original, noise]))
    assert not any(d.title == "封面字段：签订地点" for d in diffs)


def test_cover_next_field_ends_blank_value_search():
    labels = [block("place", "签订地点", (115, 553, 177, 574)),
              block("date", "签订日期", (115, 580, 177, 600))]
    date = block("date-value", "2026年10月20日", (115, 603, 250, 623))
    diffs = CoverMetadataComparator().build_diffs(document(labels), document([*labels, date]))
    assert not any(d.title == "封面字段：签订地点" for d in diffs)
    assert any(d.title == "封面字段：签订日期" for d in diffs)


def test_cover_remote_untyped_fragment_is_not_a_field_value():
    label = block("place", "签订地点", (115, 553, 177, 574))
    fragment = block("edge", "北工型", (567, 603, 595, 685), confidence=0.3586)
    diffs = CoverMetadataComparator().build_diffs(document([label]), document([label, fragment]))
    assert not any(d.title == "封面字段：签订地点" for d in diffs)


@pytest.mark.parametrize("box", [(210, 553, 250, 574), (115, 580, 160, 600)])
def test_cover_real_adjacent_place_addition_survives(box):
    label = block("place", "签订地点", (115, 553, 177, 574))
    value = block("value", "北京", box)
    diffs = CoverMetadataComparator().build_diffs(document([label]), document([label, value]))
    assert any(d.title == "封面字段：签订地点" and d.compare_text == "北京" for d in diffs)


def signing(label_kind="text", label_text="（盖章）", label_confidence=0.99,
            peer_y=148, include_label=True):
    blocks = [block("left", "甲方：测试甲有限公司", (87, 117, 309, 135)),
              block("right", "乙方：测试乙有限公司", (330, 117, 540, 135)),
              block("address", "地址：测试地址", (87, 180, 280, 198)),
              block("right-label", "(盖章)", (368, peer_y, 415, peer_y + 18))]
    if include_label:
        blocks.append(block("left-label", label_text, (144, 149, 183, 165), label_kind, label_confidence))
    sb = SigningBlock(block_id="SB", page_no=1, bbox=BBox(x0=70, y0=90, x1=565, y1=230),
                      block_role="both_parties", confidence=1,
                      confidence_reasons=["two_column_layout"], source_block_ids=[b.block_id for b in blocks])
    return SigningRegionExtractor().extract_from_blocks([sb], document(blocks))[0]


@pytest.mark.parametrize("kind", ["seal", "stamp"])
def test_observed_printed_label_under_stamp_does_not_become_deletion(kind):
    original, compare = signing(), signing(kind, "盖章）", 0.9884)
    party = next(e for e in compare.elements if e.element_type == "party_field" and e.raw_ref["party_role"] == "甲方")
    assert party.text.endswith("盖章）")
    assert "left-label" in party.raw_ref["source_block_ids"]
    result = SigningRegionComparator().compare(original, compare)
    assert not result.label_changes
    assert not result.party_changes


@pytest.mark.parametrize("kwargs", [
    {"label_text": "合同专用章"}, {"label_text": "盖章"},
    {"label_confidence": 0.35}, {"peer_y": 210},
])
def test_uncorroborated_stamp_reading_cannot_restore_a_printed_label(kwargs):
    compare = signing("seal", **kwargs)
    party = next(e for e in compare.elements if e.element_type == "party_field" and e.raw_ref["party_role"] == "甲方")
    assert party.text == "甲方：测试甲有限公司"


def test_actual_missing_label_is_still_reported():
    result = SigningRegionComparator().compare(signing(), signing(include_label=False))
    assert len(result.label_changes) == 1
    assert result.label_changes[0]["party_role"] == "甲方"
