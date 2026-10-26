import pytest

from app.config_models import DocumentUnderstandingSettings
from app.models import BBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.document_understanding import DocumentUnderstandingService
from app.services.signing_region.block_detector import SigningBlockDetector
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.extractor import SigningRegionExtractor
from app.services.signing_region.matcher import SigningRegionMatcher


def block(id, text, x, y, right, *, kind="text"):
    return TextBlock(
        block_id=id,
        page_no=18,
        text=text,
        bbox=BBox(x0=x, y0=y, x1=right, y1=y + 14),
        block_type=kind,
        block_role=kind,
        flow_role="non_text" if kind == "formula" else "body",
        confidence=0.999,
    )


def doc(blocks, page=18):
    for b in blocks:
        b.page_no = page
    return Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=40,
        pages=[Page(page_no=page, width=600, height=820, blocks=blocks)],
    )


def test_formula_indicator_survives_semantic_and_clause_pipeline():
    d = doc(
        [
            block("h", "4.1.3.4 系统实时性指标", 110, 470, 280),
            block("a", "实时数据刷新周期≤5秒", 110, 498, 300),
            block("b", "画面调用响应时间≤4秒", 110, 526, 300, kind="formula"),
            block("c", "联机检索数据的平均响应时间≤5秒", 110, 554, 340),
        ]
    )
    DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(d, "compare")
    clauses = ClauseSplitter().split(d, "N")
    assert "画面调用响应时间≤4秒" in clauses[0].text
    assert d.pages[0].blocks[2].block_id == "b"


@pytest.mark.parametrize("text", ["E=mc²", r"\\frac{分子}{分母}", "画面调用响应时间"])
def test_non_prose_formula_not_blindly_promoted(text):
    d = doc([block("f", text, 110, 526, 300, kind="formula")])
    DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(d, "compare")
    assert not ClauseSplitter().split(d, "N")


def signing(merged, date="年月日", company="国能日新科技股份有限公司"):
    a = "甲方：定边县瑞能新能源科技有限公司"
    b = "乙方：" + company
    blocks = (
        [block("ab", a + b, 90, 600, 500)] if merged else [block("a", a, 90, 600, 294), block("b", b, 320, 600, 500)]
    )
    return doc([*blocks, block("ad", date, 110, 655, 220), block("bd", "年月日", 340, 655, 430)], 38)


def regions(d):
    structure = SigningBlockDetector().detect(d)
    return structure, SigningRegionExtractor().extract_from_blocks(structure.blocks, d)


def test_merged_party_line_matches_split_form_and_owns_dates():
    old, ors = regions(signing(False))
    new, nrs = regions(signing(True, "2026年05月22日"))
    assert len(ors) == len(nrs) == 1
    assert {"ad", "bd"} <= set(new.blocks[0].source_block_ids)
    pairs = SigningRegionMatcher().match(ors, nrs)
    assert len(pairs) == 1 and pairs[0][1] is not None
    result = SigningRegionComparator().compare(*pairs[0][:2], match_confidence=pairs[0][2])
    assert not result.region_changes
    fields = [e for e in nrs[0].elements if e.raw_ref.get("field_key") == "date"]
    assert any("2026" in e.text and e.raw_ref.get("party_role") == "甲方" for e in fields)
    assert all("公司" not in e.text for e in fields)


def test_real_company_change_survives_merged_form():
    _, ors = regions(signing(False))
    _, nrs = regions(signing(True, company="国能日新科技有限公司"))
    assert len(nrs) == 1
    pair = SigningRegionMatcher().match(ors, nrs)[0]
    result = SigningRegionComparator().compare(*pair[:2], match_confidence=pair[2])
    assert result.party_changes


def test_body_party_mentions_do_not_create_form():
    d = doc([block("p", "甲方：负责配合项目验收，乙方：负责完成项目实施工作。", 90, 600, 500)])
    assert not SigningBlockDetector().detect(d).blocks


def test_form_does_not_absorb_distant_dates():
    d = signing(True)
    d.pages[0].blocks[-1].bbox.y0 = 780
    d.pages[0].blocks[-1].bbox.y1 = 795
    s, _ = regions(d)
    assert s.blocks and "bd" not in s.blocks[0].source_block_ids


def test_paired_recovery_requires_unique_aligned_same_company_evidence():
    from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus
    from app.services.signing_region.paired_recovery import recover_paired_forms
    from app.services.signing_region.block_detector import SigningBlockDetectionResult

    left = signing(False)
    right = signing(True)
    det = SigningBlockDetector()
    ls = det.detect(left)
    candidate = dict(
        page_no=38,
        score=0.4,
        reasons=["paired_parties", "bottom_signing_position"],
        block_ids=["ab"],
        text=right.pages[0].blocks[0].text,
        bbox=right.pages[0].blocks[0].bbox.model_dump(),
    )
    alignment = PageAlignmentResult((PageAlignmentEntry(PageAlignmentStatus.MATCHED, (38,), (38,), 0.99, (), "test"),))
    rs = SigningBlockDetectionResult(low_confidence_candidates=[candidate])
    assert recover_paired_forms(det, left, right, ls, rs, alignment)
    assert len(rs.blocks) == 1
    assert rs.blocks[0].block_role.value == "both_parties"
    # A genuinely absent region must not be invented from the opposite PDF.
    missing = SigningBlockDetectionResult()
    assert not recover_paired_forms(det, left, right, ls, missing, alignment)
    assert not missing.blocks
    wrong = dict(candidate, text=candidate["text"].replace("股份有限公司", "有限公司"))
    rs = SigningBlockDetectionResult(low_confidence_candidates=[wrong])
    assert not recover_paired_forms(det, left, right, ls, rs, alignment)
    rs = SigningBlockDetectionResult(low_confidence_candidates=[candidate, dict(candidate)])
    assert not recover_paired_forms(det, left, right, ls, rs, alignment)
    rs = SigningBlockDetectionResult(low_confidence_candidates=[candidate])
    assert not recover_paired_forms(det, left, right, ls, rs, None)


def test_owned_dates_do_not_reenter_clause_but_date_change_remains():
    from app.services.signing_region.clause_document import SigningClauseDocumentBuilder
    from app.services.signing_region.printed_ownership import exclude_equal_signing_prose
    from app.services.signing_region.diff_builder import SigningRegionDiffBuilder

    left = signing(False)
    right = signing(True, "2026年05月22日")
    ls, lr = regions(left)
    rs, rr = regions(right)
    builder = SigningClauseDocumentBuilder()
    lc = builder.build(left, ls.blocks)
    rc = builder.build(right, rs.blocks)
    pairs = SigningRegionMatcher().match(lr, rr)
    results = [SigningRegionComparator().compare(a, b, match_confidence=c) for a, b, c in pairs]
    changes = SigningRegionDiffBuilder().build_diffs(results)
    assert any(d.diff_type == "ADD" and "2026" in d.compare_text for d in changes)
    exclude_equal_signing_prose(lc, rc, pairs, ls.blocks, rs.blocks)
    assert not lc.document.pages[0].blocks and not rc.document.pages[0].blocks
    assert len(left.pages[0].blocks) == 4 and len(right.pages[0].blocks) == 3


def test_indicator_real_threshold_change_still_compares():
    from app.services.matcher import ClauseMatcher
    from app.services.diff_engine import DiffEngine

    documents = [
        doc(
            [
                block("h", "4.1.3.4 系统实时性指标", 110, 470, 280),
                block("v", f"画面调用响应时间≤{n}秒", 110, 500, 300, kind="formula"),
            ]
        )
        for n in [4, 5]
    ]
    understanding = DocumentUnderstandingService(DocumentUnderstandingSettings())
    for d in documents:
        understanding.understand(d, "test")
    clauses = [ClauseSplitter().split(d, p) for d, p in zip(documents, ["O", "N"])]
    diffs = DiffEngine().build_diffs(ClauseMatcher().match(*clauses))
    assert len(diffs) == 1 and diffs[0].diff_type == "MODIFY"
    assert "4" in diffs[0].original_snippet and "5" in diffs[0].compare_snippet


def test_scan_edge_glyph_not_mistaken_for_body_but_inline_letter_kept():
    d = doc([block("edge", "à", 575, 590, 581), block("body", "A", 110, 590, 116)])
    for b in d.pages[0].blocks:
        b.source = "PP-OCRv6"
    DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(d, "compare")
    assert d.pages[0].blocks[0].enter_clause_compare is False
    assert d.pages[0].blocks[1].enter_clause_compare is True
