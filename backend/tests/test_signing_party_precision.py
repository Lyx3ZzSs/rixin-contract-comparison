import pytest

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.diff_builder import SigningRegionDiffBuilder
from app.services.signing_region.extractor import SigningRegionExtractor
from app.services.signing_region.models import SigningBlock, SigningElement, SigningRegion
from app.services.signing_region.value_ocr import SigningFieldValueOCR
from tests.test_signing_region_value_ocr import _FakeOCRExtractor, _pdf


NAME = "江苏东大金智信息系统有限公司"
MISREAD = "江苏东达全智信息系统有限公司"


def region(name=NAME, suffix="（盖章）"):
    box = BBox(x0=80, y0=100, x1=290, y1=120)
    block = TextBlock(block_id="party", page_no=1, text=f"甲方：{name}", bbox=box,
                      block_type="text", source="ppocrv5", confidence=.97)
    signing = SigningBlock(block_id="SB", page_no=1, bbox=BBox(x0=60,y0=90,x1=510,y1=210),
                           source_block_ids=["party"])
    party = SigningRegionExtractor()._party_field_elements(signing, {"party": block})[0]
    if suffix:
        residual = SigningElement(element_id="suffix", element_type="label", page_no=1,
            bbox=BBox(x0=80,y0=130,x1=125,y1=150), text=suffix,
            raw_ref={"party_role":"甲方", "residual_kind":"party_suffix"})
        SigningRegionExtractor._merge_party_residuals([party], [residual])
    seal = SigningElement(element_id="seal", element_type="seal", page_no=1,
        bbox=BBox(x0=130,y0=95,x1=210,y1=125), source="visual_model")
    return SigningRegion(region_id="SR", page_no=1, bbox=signing.bbox, elements=[party,seal])


def test_changed_company_does_not_paint_equal_stamp_instruction():
    comparison = SigningRegionComparator().compare(region(), region(MISREAD, "(盖章)"))
    diff = next(d for d in SigningRegionDiffBuilder().build_diffs([comparison]) if d.section_type == "signature:party")
    assert [diff.original_text[r.start:r.end] for r in diff.original_change_ranges] == ["大金"]
    assert [diff.compare_text[r.start:r.end] for r in diff.compare_change_ranges] == ["达全"]
    assert all("盖章" not in e.text for e in diff.original_evidence + diff.compare_evidence)
    assert len(diff.compare_evidence) == 1
    assert diff.compare_evidence[0].bbox.y1 == 120


def test_only_bracket_width_change_is_equal():
    comparison = SigningRegionComparator().compare(region(), region(suffix="(盖章)"))
    assert not comparison.party_changes


def test_normal_text_party_has_bounded_name_roi():
    party = region().elements[0]
    assert party.raw_ref["field_key"] == "party_name"
    roi = BBox.model_validate(party.raw_ref["value_roi"])
    assert roi.y1 <= 120
    assert roi.x1 <= 290
    assert roi.x0 > 80


def support(name=NAME):
    return Document(filename="own.pdf", path="own.pdf", page_count=2, pages=[
        Page(page_no=2,width=600,height=800,blocks=[TextBlock(block_id="own-reference",page_no=2,
            text=f"甲方：{name}",bbox=BBox(x0=80,y0=100,x1=300,y1=120),confidence=.99,source="ppocrv5")])])


def test_local_company_recovery_preserves_suffix_and_geometry(tmp_path):
    r = region(MISREAD)
    service = SigningFieldValueOCR(extractor=_FakeOCRExtractor([NAME, NAME]))
    result = service.enrich_occluded_fields(_pdf(tmp_path), [r], source_document=support())
    assert result[0]["status"] == "RECOVERED"
    party = r.elements[0]
    assert party.text == f"甲方：{NAME}（盖章）"
    assert "".join(party.raw_ref["field_segments"]) == party.text
    assert party.raw_ref["field_segments"][-1] == "（盖章）"
    assert len(party.raw_ref["field_bboxes"]) == len(party.raw_ref["field_segments"])
    assert not SigningRegionComparator().compare(region(), r).party_changes


@pytest.mark.parametrize("readings,reference", [([NAME, MISREAD], NAME), ([NAME, NAME], MISREAD)])
def test_conflicting_local_company_is_not_silently_confirmed(tmp_path, readings, reference):
    r = region(MISREAD)
    service = SigningFieldValueOCR(extractor=_FakeOCRExtractor(readings))
    service.enrich_occluded_fields(_pdf(tmp_path), [r], source_document=support(reference))
    assert r.elements[0].text == f"甲方：{MISREAD}（盖章）"
    comparison = SigningRegionComparator().compare(region(), r)
    diff = next(d for d in SigningRegionDiffBuilder().build_diffs([comparison]) if d.section_type == "signature:party")
    assert diff.content_recognition_status == "NEEDS_REVIEW"


def test_verified_real_company_change_survives(tmp_path):
    changed = "江苏东大科技信息系统有限公司"
    r = region(changed)
    service = SigningFieldValueOCR(extractor=_FakeOCRExtractor([changed, changed]))
    service.enrich_occluded_fields(_pdf(tmp_path), [r], source_document=support(changed))
    comparison = SigningRegionComparator().compare(region(), r)
    assert len(comparison.party_changes) == 1
    diff = next(d for d in SigningRegionDiffBuilder().build_diffs([comparison]) if d.section_type == "signature:party")
    assert "科技" in diff.compare_text
    assert all("盖章" not in e.text for e in diff.compare_evidence)


def test_real_stamp_instruction_deletion_is_preserved():
    comparison = SigningRegionComparator().compare(region(), region(suffix=""))
    diff = next(d for d in SigningRegionDiffBuilder().build_diffs([comparison]) if d.section_type == "signature:label")
    assert [diff.original_text[r.start:r.end] for r in diff.original_change_ranges] == ["（盖章）"]
    assert [e.text for e in diff.original_evidence] == ["（盖章）"]
    assert not diff.compare_evidence


def test_character_geometry_only_highlights_changed_company_characters():
    a, b = region(), region(MISREAD)
    for r in (a, b):
        party = r.elements[0]
        party.raw_ref["field_char_boxes"] = [CharBox(char=c,page_no=1,text_index=i,
            bbox=BBox(x0=80+i*10,y0=100,x1=90+i*10,y1=120)).model_dump()
            for i,c in enumerate(party.raw_ref["field_segments"][0])]
    comparison = SigningRegionComparator().compare(a,b)
    diff = next(d for d in SigningRegionDiffBuilder().build_diffs([comparison]) if d.section_type == "signature:party")
    assert "".join(e.text for e in diff.original_evidence) == "大金"
    assert "".join(e.text for e in diff.compare_evidence) == "达全"


def test_inline_instruction_is_outside_name_roi_and_highlight():
    a, b = region(NAME + "（盖章）", suffix=""), region(MISREAD + "(盖章)", suffix="")
    party = b.elements[0]
    assert party.raw_ref["field_segments"] == [f"甲方：{MISREAD}", "(盖章)"]
    assert party.raw_ref["value_roi"]["x1"] <= party.raw_ref["field_bboxes"][1]["x0"]
    diff = next(d for d in SigningRegionDiffBuilder().build_diffs([SigningRegionComparator().compare(a,b)])
                if d.section_type == "signature:party")
    assert len(diff.compare_evidence) == 1
    assert "盖章" not in diff.compare_evidence[0].text


def test_two_parties_and_wrapped_name_keep_separate_rois():
    blocks = [
        TextBlock(block_id="row",page_no=1,text=f"甲方：{NAME}乙方：国能日新科技股份有限公司",
                  bbox=BBox(x0=80,y0=100,x1=500,y1=120)),
        TextBlock(block_id="continuation",page_no=1,text="随县分公司",
                  bbox=BBox(x0=80,y0=123,x1=160,y1=140)),
        TextBlock(block_id="prompt",page_no=1,text="（盖章）",
                  bbox=BBox(x0=80,y0=145,x1=125,y1=160)),
    ]
    signing = SigningBlock(block_id="SB",page_no=1,bbox=BBox(x0=60,y0=90,x1=510,y1=210),
                           source_block_ids=[b.block_id for b in blocks])
    parties = SigningRegionExtractor()._party_field_elements(signing,{b.block_id:b for b in blocks})
    a,b = parties
    assert a.text == f"甲方：{NAME}随县分公司"
    assert b.text == "乙方：国能日新科技股份有限公司"
    assert a.raw_ref["value_roi"]["y1"] == 140
    assert a.raw_ref["value_roi"]["x1"] < b.raw_ref["value_roi"]["x0"]
    assert b.raw_ref["value_roi"]["y1"] == 120


def test_unoccluded_party_does_not_trigger_extra_ocr(tmp_path):
    r = region(MISREAD)
    r.elements = r.elements[:1]
    assert SigningFieldValueOCR(extractor=_FakeOCRExtractor([])).enrich_occluded_fields(
        _pdf(tmp_path),[r],source_document=support()) == []
