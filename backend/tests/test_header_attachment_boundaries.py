"""Native/OCR segmentation must not manufacture additions or hide real edits."""

import pytest

from app.models import BBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.diff_engine import DiffEngine
from app.services.document_preparation import DocumentPreparer
from app.services.header_footer_compare import HeaderFooterComparator
from app.services.matcher import ClauseMatcher


def block(id, text, page, x, y, width=180, kind="text"):
    return TextBlock(
        block_id=id, text=text, page_no=page, block_type=kind, bbox=BBox(x0=x, y0=y, x1=x + width, y1=y + 14)
    )


def document(pages):
    return Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=len(pages),
        pages=[Page(page_no=i, width=600, height=840, blocks=bs) for i, bs in enumerate(pages, 1)],
    )


def headers(split, *, changed=False):
    pages = []
    for n in range(1, 4):
        number = "ABC/23-004" if not changed or n != 3 else "ABC/23-005"
        bs = (
            [block(f"n{n}", number, n, 330, 35, 85, "header"), block(f"t{n}", "技术服务合同", n, 430, 35, 90, "header")]
            if split
            else [block(f"h{n}", number + " 技术服务合同", n, 330, 35, 190, "text" if n == 2 else "header")]
        )
        bs += [
            block(f"b{n}", "1.1 本合同费用为100元。", n, 60, 220, 340),
            block(f"a{n}", "7号示例园区N6", n, 300, 320, 160),
        ]
        pages.append(bs)
    # Equal words in the body must remain available to clause comparison.
    pages[1].append(block("body-copy", "ABC/23-004 技术服务合同", 2, 60, 450, 250))
    return document(pages)


def test_header_line_equivalence_and_shared_body_ownership():
    left, right = headers(True), headers(False)
    preparer = DocumentPreparer()
    preparer.prepare_pair(left, right)
    assert HeaderFooterComparator().build_diffs(left, right) == []
    units = ClauseSplitter()._collect_units(right)
    assert "h2" not in {u.block_id for u in units}
    assert "body-copy" in {u.block_id for u in units}
    assert "a2" in {u.block_id for u in units}


def test_single_page_changed_header_survives_line_normalization():
    left, right = headers(True), headers(False, changed=True)
    DocumentPreparer().prepare_pair(left, right)
    diffs = HeaderFooterComparator().build_diffs(left, right)
    assert len(diffs) == 1
    assert "ABC/23-005" in diffs[0].compare_text
    assert {e.page_no for e in diffs[0].compare_evidence} == {3}
    assert any("ABC/23-004" in e.text for e in diffs[0].original_evidence)


def test_split_header_evidence_keeps_each_source_box():
    left, right = headers(True), headers(False, changed=True)
    diffs = HeaderFooterComparator().build_diffs(left, right)
    assert len(diffs) == 1
    assert any(e.bbox.x0 == 330 and e.bbox.x1 == 415 for e in diffs[0].original_evidence)
    assert any(e.bbox.x0 == 430 and e.bbox.x1 == 520 for e in diffs[0].original_evidence)


def attachment(kind, *, changed=False, extra=False):
    pages = [
        [
            block("appendix2", "附件二", 1, 60, 90, 100, "paragraph_title"),
            block("oldtitle", "技术服务内容", 1, 200, 120, 200, "paragraph_title"),
            block("old", "4. 项目服务期限及验收", 1, 60, 220, 300, "paragraph_title"),
            block("oldbody", "服务期限为一年，项目验收后支付。", 1, 60, 260, 420),
        ],
        [
            block("appendix3", "附件三", 2, 60, 90, 100, "paragraph_title"),
            block("title", "保密合同", 2, 230, 120, 140, kind),
            block("party", "甲方：示例科技有限公司", 2, 60, 200, 380),
            block("intro", "双方协商一致，订立本合同。", 2, 60, 245, 380),
            block("term", "1. 保密期限", 2, 60, 300, 200, "paragraph_title"),
            block("body", "乙方应承担保密义务，期限为" + ("五" if changed else "三") + "年。", 2, 60, 350, 420),
        ],
    ]
    if extra:
        pages.append(
            [
                block("a4", "附件四", 3, 60, 90, 100, "paragraph_title"),
                block("extra", "补充服务协议", 3, 230, 120, 200, "doc_title"),
                block("new", "乙方提供额外技术服务。", 3, 60, 200, 350),
            ]
        )
    return document(pages)


def compare_clauses(left, right):
    DocumentPreparer().prepare_pair(left, right)
    splitter = ClauseSplitter()
    lc, rc = splitter.split(left, "O"), splitter.split(right, "N")
    return lc, rc, DiffEngine().build_diffs(ClauseMatcher().match(lc, rc))


@pytest.mark.parametrize("kind", ["doc_title", "title"])
def test_attachment_entry_is_independent_of_native_title_type(kind):
    lc, rc, diffs = compare_clauses(attachment(kind), attachment("paragraph_title"))
    assert diffs == []
    for cs in [lc, rc]:
        previous = next(c for c in cs if c.clause_no == "4")
        assert previous.page_numbers == [1]
        assert "附件三" not in previous.text
        assert any("附件三" in c.text and "保密合同" in c.text for c in cs)


def test_real_confidentiality_change_and_new_attachment_are_retained():
    _, _, diffs = compare_clauses(attachment("doc_title"), attachment("paragraph_title", changed=True, extra=True))
    assert any("五年" in d.compare_text and "三年" in d.original_text for d in diffs)
    assert any(d.diff_type == "ADD" and "额外技术服务" in d.compare_text for d in diffs)


def test_header_line_grouping_tolerates_small_vertical_ocr_jitter():
    left, right = headers(True), headers(False)
    for page in left.pages:
        page.blocks[1].bbox.y0 -= 1
        page.blocks[1].bbox.y1 -= 1
    assert HeaderFooterComparator().build_diffs(left, right) == []


def test_one_off_top_body_and_midpage_attachment_reference_are_preserved():
    doc = document(
        [
            [
                block("top", "合同编号：临时业务号", 1, 60, 30, 240),
                block("body", "4. 本合同附件说明", 1, 60, 260, 300, "paragraph_title"),
                block("ref", "附件三", 1, 60, 500, 100, "paragraph_title"),
                block("title", "技术资料清单", 1, 60, 530, 200, "paragraph_title"),
            ]
        ]
    )
    DocumentPreparer().prepare(doc, "original")
    assert doc.pages[0].blocks[0].enter_clause_compare is not False
    assert "document_entry_boundary" not in doc.pages[0].blocks[2].semantic_reasons


def test_attachment_entry_tolerates_small_overlap_between_ocr_heading_boxes():
    left, right = attachment("doc_title"), attachment("doc_title")
    right.pages[1].blocks[0].bbox.y1 = 122
    lc, rc, diffs = compare_clauses(left, right)
    assert diffs == []
    assert all("保密合同" not in next(c for c in cs if c.clause_no == "4").text for cs in [lc, rc])


def test_attachment_title_change_does_not_change_ownership_identity():
    left, right = attachment('doc_title'), attachment('paragraph_title')
    right.pages[1].blocks[1].text = '保密协议'
    _, _, diffs = compare_clauses(left, right)
    assert any(d.diff_type == 'MODIFY' and '保密合同' in d.original_text and '保密协议' in d.compare_text for d in diffs)
    assert not any(d.diff_type in {'ADD', 'DELETE'} for d in diffs)
