"""Heading omissions and split drift must not become contractual deletions."""

import pytest

from app.models import BBox, Clause, Document, EvidenceBox, Page, TextBlock, TextRange, DiffItem
from app.services.clause_splitter import ClauseSplitter
from app.services.document_preparation import DocumentPreparer
from app.services.matcher import ClauseMatcher
from app.services.normalizer import TextNormalizer
from app.services.diff.facade import DiffEngine


def block(text, y, kind="text", page=1, x=80):
    return TextBlock(
        block_id=f"{page}-{y}-{kind}",
        page_no=page,
        text=text,
        block_type=kind,
        block_role="appendix_section",
        source="native_pdf",
        bbox=BBox(x0=x, y0=y, x1=x + len(text) * 10, y1=y + 12),
        confidence=1,
    )


def document(blocks, path="missing.pdf"):
    return Document(
        filename="test.pdf", path=path, page_count=1, pages=[Page(page_no=1, width=600, height=840, blocks=blocks)]
    )


def clause(text, id="C1", number="", title=""):
    n = TextNormalizer()
    return Clause(
        clause_id=id,
        clause_no=number,
        title=title,
        text=text,
        normalized_text=n.normalize_for_diff(text),
        match_text=n.normalize_for_match(text),
        section_type="appendix",
        page_numbers=[1],
        order_index=1,
    )


def test_native_appendix_titles_split_like_located_peer_without_copying_text():
    texts = [
        "1.上报日志完整记录",
        "日志须完整保留并独立导出。",
        "五、培训与文档交付",
        "1.技术文档交付",
        "乙方须交付完整技术文档。",
        "六、验收标准",
        "双方按约定标准验收。",
    ]
    a = document([block(t, 100 + i * 24) for i, t in enumerate(texts)])
    b = document(
        [block(t, 100 + i * 24, "paragraph_title" if i in (0, 2, 3, 5) else "text") for i, t in enumerate(texts)]
    )
    a.pages[0].blocks[0].block_type = "paragraph_title"
    DocumentPreparer().prepare_pair(a, b)
    ac, bc = [ClauseSplitter().split(d, p) for d, p in ((a, "O"), (b, "N"))]
    assert [c.text for c in ac] == [c.text for c in bc]
    assert len(ac) == 4
    assert [x.text for x in a.pages[0].blocks] == texts


@pytest.mark.parametrize("number", ["五", "四"])
def test_containment_keeps_source_prefix_and_recovers_seven_character_heading(number):
    parent = clause(f"日志正文。\n{number}、培训与文档交付\n1.技术文档交付\n乙方应交付完整文档。", "O")
    targets = [
        clause("五、培训与文档交付", "N1", "五", "培训与文档交付"),
        clause("1.技术文档交付\n乙方应交付完整文档。", "N2", "1", "技术文档交付"),
    ]
    m = ClauseMatcher()
    consumed = {}
    pairs = m._match_contained_numbered_clauses([parent], targets, set(), consumed)
    assert len(pairs) == 2
    assert pairs[0].original.text == f"{number}、培训与文档交付"
    assert pairs[1].original.text.startswith("1.")
    assert m._redact_clause(parent, consumed).text == "日志正文。"
    diffs = DiffEngine().build_diffs(pairs)
    assert bool(diffs) == (number != "五")


def test_short_heading_inside_prose_is_not_consumed_as_heading():
    parent = clause("双方约定培训与文档交付必须同时完成，不得延误。", "O")
    target = clause("五、培训与文档交付", "N", "五", "培训与文档交付")
    assert not ClauseMatcher()._match_contained_numbered_clauses([parent], [target], set(), {})


def test_repeated_short_heading_is_ambiguous():
    parent = clause("正文。\n五、培训与文档交付\n其他正文。\n五、培训与文档交付", "O")
    target = clause("五、培训与文档交付", "N", "五", "培训与文档交付")
    assert not ClauseMatcher()._match_contained_numbered_clauses([parent], [target], set(), {})


def test_duplicate_peer_heading_does_not_promote_body_role():
    from app.services.heading_recovery import reconcile_numbered_heading_roles

    own = document([block("五、培训与文档交付", 100)])
    peer = document(
        [block("五、培训与文档交付", 100, "paragraph_title"), block("五、培训与文档交付", 120, "paragraph_title")]
    )
    assert reconcile_numbered_heading_roles(own, peer) == []
    assert own.pages[0].blocks[0].block_type == "text"


def test_role_recovery_preserves_real_number_change_through_matching():
    from app.services.heading_recovery import reconcile_numbered_heading_roles

    own = document([block("五、培训与文档交付", 100)])
    peer = document([block("六、培训与文档交付", 100, "paragraph_title")])
    reconcile_numbered_heading_roles(own, peer)
    original, compare = [ClauseSplitter().split(d, p) for d, p in ((own, "O"), (peer, "N"))]
    pairs = ClauseMatcher().match(original, compare)
    diffs = DiffEngine().build_diffs(pairs)
    assert diffs
    assert own.pages[0].blocks[0].text == "五、培训与文档交付"
    assert any(d.original_change_ranges or d.compare_change_ranges for d in diffs)


@pytest.mark.parametrize(
    "peer_text,peer_y,expected",
    [
        ("六、验收标准", 100, True),
        ("七、验收标准", 100, False),
        ("六、验收标准", 500, False),
        ("六、验收要求", 100, False),
    ],
)
def test_modify_heading_residual_requires_same_full_located_heading(peer_text, peer_y, expected):
    from app.services.heading_recovery import reconcile_heading_residuals

    a = document([block("六、验收标准", 100)])
    b = document([block(peer_text, peer_y, "paragraph_title")])
    d = DiffItem(
        diff_id="d",
        diff_type="MODIFY",
        source_type="clause",
        section_type="appendix",
        original_text="日志正文\n六、",
        compare_text="日志正文",
        original_snippet="六、",
        original_change_ranges=[TextRange(start=5, end=7, highlight_type="DELETE")],
        original_evidence=[
            EvidenceBox(page_no=1, bbox=BBox(x0=80, y0=100, x1=100, y1=112), text="六、", highlight_type="DELETE")
        ],
    )
    remaining, changes = reconcile_heading_residuals([d], a, b)
    assert (not remaining) == expected
    assert bool(changes) == expected


def test_heading_residual_preserves_unrelated_amount_change():
    from app.services.heading_recovery import reconcile_heading_residuals

    a = document([block("六、验收标准", 100)])
    b = document([block("六、验收标准", 100, "paragraph_title")])
    d = DiffItem(
        diff_id="d",
        diff_type="MODIFY",
        source_type="clause",
        section_type="appendix",
        original_text="六、\n金额100元",
        compare_text="金额200元",
        original_change_ranges=[
            TextRange(start=0, end=2, highlight_type="DELETE"),
            TextRange(start=5, end=8, highlight_type="MODIFY"),
        ],
        compare_change_ranges=[TextRange(start=2, end=5, highlight_type="MODIFY")],
        original_evidence=[
            EvidenceBox(page_no=1, bbox=BBox(x0=80, y0=100, x1=100, y1=112), text="六、", highlight_type="DELETE")
        ],
    )
    remaining, _ = reconcile_heading_residuals([d], a, b)
    assert len(remaining) == 1
    assert remaining[0].original_change_ranges == [TextRange(start=5, end=8, highlight_type="MODIFY")]
    assert remaining[0].compare_change_ranges == d.compare_change_ranges


@pytest.mark.parametrize(
    "readings,expected",
    [
        (["一、系统开放性要求"] * 2, "一、系统开放性要求"),
        (["二、系统开放性要求"] * 2, "二、系统开放性要求"),
        (["一、系统开放性要求", "二、系统开放性要求"], "系统开放性要求"),
        (["系统开放性要求"] * 2, "系统开放性要求"),
        (["一、系统封闭性要求"] * 2, "系统开放性要求"),
    ],
)
def test_ocr_prefix_requires_own_consistent_readings(tmp_path, readings, expected):
    from app.services.heading_recovery import HeadingPrefixOCR

    path = tmp_path / "own.pdf"
    path.touch()
    b = block("系统开放性要求", 100, x=100)
    b.source = "PP-OCRv6"
    own = document([b], str(path))
    peer = document([block("一、系统开放性要求", 100, "paragraph_title")])
    service = HeadingPrefixOCR()

    def read(*args):
        return [block(t, 100, x=80) for t in readings]

    service._read = read
    service.enrich_pair(peer, own)
    assert b.text == expected
    if expected.startswith("二"):
        assert b.text != peer.pages[0].blocks[0].text
