"""Structure recovery must preserve real changes and original evidence."""

import pytest
from app.models import BBox, Clause, Document, EvidenceBox, Page, TextBlock
from app.services.matcher import ClauseMatcher
from app.services.diff_engine import DiffEngine
from app.services.normalizer import TextNormalizer
from app.services.signing_region.extractor import SigningRegionExtractor
from app.services.signing_region.models import SigningBlock
from app.services.signing_region.clause_document import SigningClauseDocumentBuilder


def clause(id, text, order=0, instance="附件三:保密合同"):
    return Clause(
        clause_id=id,
        text=text,
        normalized_text=TextNormalizer().normalize(text),
        match_text=TextNormalizer().normalize_for_match(text),
        title=text.splitlines()[0],
        clause_no="4" if order == 0 else "",
        section_type="appendix",
        order_index=order,
        page_numbers=[1],
        source_block_ids=[id],
        bboxes=[EvidenceBox(page_no=1, bbox=BBox(x0=10, y0=40 + order * 60, x1=500, y1=80 + order * 60), text=text)],
        split_flags=["ATTACHMENT_INSTANCE:" + instance],
    )


FIRST = "4. 项目服务期限及验收\n本项目的服务期限为一年，验收后支付相关服务费用。"
SECOND = "保密事项\n乙方应当保护在项目实施中获知的保密信息，保密期限为三年。"
THIRD = "资料保管\n双方交付的全部技术资料均应存档备查，未经同意不得复制。"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("count", [2, 3])
@pytest.mark.parametrize("changed", [False, True])
def test_one_to_many_recovery_preserves_changes(reverse, count, changed):
    texts = [FIRST, SECOND, THIRD][:count]
    whole = clause("O1", "\n".join(texts))
    parts = [clause("N" + str(i), t.replace("三年", "五年") if changed else t, i) for i, t in enumerate(texts)]
    left, right = ([whole], parts) if not reverse else (parts, [whole])
    pairs = ClauseMatcher().match(left, right)
    diffs = DiffEngine().build_diffs(pairs)
    assert not any(d.diff_type in {"ADD", "DELETE"} for d in diffs)
    assert any(p.match_method == "adjacent_scope_recovery" for p in pairs)
    if changed:
        assert len(diffs) == 1
        assert "三年" in (diffs[0].original_text if not reverse else diffs[0].compare_text)
        assert "五年" in (diffs[0].compare_text if not reverse else diffs[0].original_text)
    else:
        assert not diffs
    assert whole.text == "\n".join(texts)
    assert whole.source_block_ids == ["O1"]


def test_recovery_does_not_cross_attachment_or_hide_new_paragraph():
    whole = clause("O", FIRST + "\n" + SECOND)
    parts = [clause("N0", FIRST), clause("N1", SECOND, 1, instance="附件四:其他合同")]
    pairs = ClauseMatcher().match([whole], parts)
    assert not any(p.match_method == "adjacent_scope_recovery" for p in pairs)
    whole = clause("O", FIRST)
    parts = [clause("N0", FIRST), clause("N1", SECOND, 1)]
    assert any(d.diff_type == "ADD" for d in DiffEngine().build_diffs(ClauseMatcher().match([whole], parts)))


def block(id, text, y, x=300):
    return TextBlock(block_id=id, page_no=1, text=text, block_type="text", bbox=BBox(x0=x, y0=y, x1=x + 240, y1=y + 14))


def test_address_continuation_owned_only_after_field_extraction():
    blocks = [
        block("address", "地址：北京市示例路2", 100),
        block("tail", "7号园区N6", 122),
        block("contact", "联系人：张三", 145),
        block("unrelated", "后续独立文字", 167),
        block("other-column", "对方文字", 122, 30),
    ]
    doc = Document(filename="x", path="x", page_count=1, pages=[Page(page_no=1, width=600, height=840, blocks=blocks)])
    signing = SigningBlock(
        block_id="S",
        page_no=1,
        bbox=BBox(x0=290, y0=90, x1=550, y1=170),
        source_block_ids=["address", "contact"],
        text="地址：北京市示例路2\n联系人：张三",
        confidence=0.95,
        exclude_from_clause_diff=True,
    )
    regions = SigningRegionExtractor().extract_from_blocks([signing], doc)
    address = next(e for e in regions[0].elements if e.raw_ref.get("field_key") == "address")
    assert address.text == "北京市示例路27号园区N6"
    assert address.raw_ref["source_block_ids"] == ["address", "tail"]
    assert len(address.raw_ref["field_bboxes"]) == 2
    kept = SigningClauseDocumentBuilder().build(doc, [signing]).document.pages[0].blocks
    assert {b.block_id for b in kept} == {"unrelated", "other-column"}


def test_recovered_scope_keeps_character_highlight_on_changed_part():
    from app.models import CharBox
    from app.services.evidence_locator import EvidenceLocator
    from app.services.pipeline_stages import _clauses_for_evidence

    whole = clause("O", FIRST + "\n" + SECOND)
    parts = [clause("N0", FIRST), clause("N1", SECOND.replace("三年", "五年"), 1)]
    for c in [whole, *parts]:
        c.char_boxes = [
            CharBox(
                char=ch,
                page_no=1,
                text_index=i,
                bbox=BBox(
                    x0=20 + i * 2,
                    y0=100 if c.clause_id != "N1" else 300,
                    x1=22 + i * 2,
                    y1=112 if c.clause_id != "N1" else 312,
                ),
            )
            for i, ch in enumerate(c.text)
        ]
    pairs = ClauseMatcher().match([whole], parts)
    diffs = DiffEngine().build_diffs(pairs)
    EvidenceLocator().locate(
        diffs,
        _clauses_for_evidence([whole], [p.original for p in pairs]),
        _clauses_for_evidence(parts, [p.compare for p in pairs]),
    )
    assert len(diffs) == 1
    assert all(abs(e.bbox.y0 - 300) < 2 for e in diffs[0].compare_evidence)
    assert all(c.bbox.y0 == 300 for e in diffs[0].compare_evidence for c in e.char_boxes)
    assert "五" in "".join(e.text for e in diffs[0].compare_evidence)


def test_duplicate_heading_is_not_used_as_unique_scope_anchor():
    whole = clause("O", FIRST + "\n" + SECOND + "\n" + SECOND)
    parts = [clause("N0", FIRST), clause("N1", SECOND, 1), clause("N2", SECOND, 2)]
    pairs = ClauseMatcher().match([whole], parts)
    assert not any(p.match_method == "adjacent_scope_recovery" for p in pairs)


def test_changed_wrapped_address_is_reported_as_field_modification():
    from app.services.signing_region.comparator import SigningRegionComparator
    from app.services.signing_region.diff_builder import SigningRegionDiffBuilder

    def region(number):
        bs = [
            block("a", "地址：北京市示例路2", 100),
            block("b", number + "号园区N6", 122),
            block("c", "联系人：张三", 145),
        ]
        d = Document(filename="x", path="x", page_count=1, pages=[Page(page_no=1, width=600, height=840, blocks=bs)])
        s = SigningBlock(
            block_id="s",
            page_no=1,
            bbox=BBox(x0=290, y0=90, x1=550, y1=160),
            source_block_ids=["a", "c"],
            text=bs[0].text + "\n" + bs[2].text,
            confidence=0.95,
        )
        return SigningRegionExtractor().extract_from_blocks([s], d)[0]

    diffs = SigningRegionDiffBuilder().build_diffs(
        [SigningRegionComparator().compare(region("7"), region("8"), match_confidence=1.0)]
    )
    assert any(d.diff_type == "MODIFY" and "27号" in d.original_text and "28号" in d.compare_text for d in diffs)
