from __future__ import annotations

from app.models import BBox, CharBox, Clause, ClausePair, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.diff_engine import DiffEngine
from app.services.evidence_locator import EvidenceLocator
from app.services.matcher import ClauseMatcher
from app.services.normalizer import TextNormalizer


def test_text_normalizer_removes_page_number_and_compacts_text() -> None:
    text = " 合同编号：ABC-1 \n 第 1 页 \n付款　期限 为  30 天。\n\n\n"
    normalized = TextNormalizer().normalize(text)
    assert "第 1 页" not in normalized
    assert "付款 期限 为 30 天。" in normalized


def test_clause_splitter_detects_numbered_clauses() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text="1. Payment\nBuyer shall pay within 30 days.\n2. Delivery\nSeller shall deliver goods.",
                        bbox=BBox(x0=10, y0=10, x1=500, y1=120),
                    )
                ],
            )
        ],
    )
    clauses = ClauseSplitter().split(document, "O")
    assert len(clauses) == 2
    assert clauses[0].clause_no == "1"
    assert "Buyer shall pay" in clauses[0].text


def test_clause_splitter_keeps_product_table_cells_in_parent_clause() -> None:
    document = Document(
        filename="table.pdf",
        path="table.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text="第一条产品名称、数量、单价及合计",
                        bbox=BBox(x0=10, y0=10, x1=500, y1=30),
                    ),
                    TextBlock(
                        block_id="p1_b2",
                        page_no=1,
                        text="产品名称\n规格型号\n数量\n单价（元）\n合计（元）\n智能电表采集终",
                        bbox=BBox(x0=10, y0=35, x1=500, y1=65),
                    ),
                    TextBlock(
                        block_id="p1_b3",
                        page_no=1,
                        text="端\nHY-EM300\n120 台\n1,230.00\n147,600.00",
                        bbox=BBox(x0=10, y0=70, x1=500, y1=100),
                    ),
                    TextBlock(
                        block_id="p1_b4",
                        page_no=1,
                        text="边缘网关\nHY-GW200\n20 台\n2,500.00\n50,000.00",
                        bbox=BBox(x0=10, y0=105, x1=500, y1=135),
                    ),
                    TextBlock(
                        block_id="p1_b5",
                        page_no=1,
                        text="第二条质量要求",
                        bbox=BBox(x0=10, y0=150, x1=500, y1=170),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 2
    assert clauses[0].clause_no == "第一条"
    assert "120 台" in clauses[0].text
    assert "1,230.00" in clauses[0].text
    assert "边缘网关" in clauses[0].text
    assert clauses[1].clause_no == "第二条"


def test_clause_matcher_matches_by_clause_number() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text="1. Payment\nBuyer shall pay within 30 days.",
                        bbox=BBox(x0=10, y0=10, x1=500, y1=80),
                    )
                ],
            )
        ],
    )
    splitter = ClauseSplitter()
    left = splitter.split(document, "O")
    right = splitter.split(document, "N")
    pairs = ClauseMatcher().match(left, right)
    assert pairs[0].match_method == "clause_no"
    assert pairs[0].score == 100


def test_diff_engine_reports_character_level_ranges() -> None:
    left = Clause(
        clause_id="O001",
        text="数量：10000",
        normalized_text="数量10000",
    )
    right = Clause(
        clause_id="N001",
        text="数量：200",
        normalized_text="数量200",
    )

    diff = DiffEngine().build_diffs([ClausePair(original=left, compare=right)])[0]

    assert diff.original_snippet == "10000"
    assert diff.compare_snippet == "200"
    assert [(item.start, item.end) for item in diff.original_change_ranges] == [(3, 8)]
    assert [(item.start, item.end) for item in diff.compare_change_ranges] == [(3, 6)]
    assert [item.highlight_type for item in diff.original_change_ranges] == ["MODIFY"]
    assert [item.highlight_type for item in diff.compare_change_ranges] == ["MODIFY"]


def test_diff_engine_classifies_insert_and_delete_ranges() -> None:
    inserted = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text="付款方式：现金", normalized_text="付款方式现金"),
                compare=Clause(clause_id="N001", text="付款方式：现金或转账", normalized_text="付款方式现金或转账"),
            )
        ]
    )[0]
    deleted = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text="付款方式：现金或转账", normalized_text="付款方式现金或转账"),
                compare=Clause(clause_id="N001", text="付款方式：现金", normalized_text="付款方式现金"),
            )
        ]
    )[0]

    assert inserted.original_change_ranges == []
    assert [item.highlight_type for item in inserted.compare_change_ranges] == ["ADD"]
    assert inserted.compare_snippet == "或转账"
    assert [item.highlight_type for item in deleted.original_change_ranges] == ["DELETE"]
    assert deleted.compare_change_ranges == []
    assert deleted.original_snippet == "或转账"


def test_evidence_locator_maps_ranges_to_character_boxes() -> None:
    text = "数量：10000"
    char_boxes = [
        CharBox(
            char=char,
            page_no=1,
            bbox=BBox(x0=10 + index * 8, y0=20, x1=16 + index * 8, y1=30),
            text_index=index,
        )
        for index, char in enumerate(text)
    ]
    clause = Clause(
        clause_id="O001",
        text=text,
        normalized_text="数量10000",
        char_boxes=char_boxes,
    )
    diff = DiffEngine().build_diffs(
        [
            ClausePair(
                original=clause,
                compare=Clause(clause_id="N001", text="数量：200", normalized_text="数量200"),
            )
        ]
    )[0]

    located = EvidenceLocator().locate([diff], [clause], [])[0]

    assert located.original_evidence[0].method == "char_exact"
    assert located.original_evidence[0].highlight_type == "MODIFY"
    assert located.original_evidence[0].text == "10000"
    assert located.original_evidence[0].bbox.x0 < char_boxes[3].bbox.x0
    assert located.original_evidence[0].bbox.x1 > char_boxes[-1].bbox.x1
    assert located.original_evidence[0].bbox.x1 - located.original_evidence[0].bbox.x0 < 50


def test_evidence_locator_splits_whitespace_and_large_gaps() -> None:
    text = "A B"
    positions = [(10, 16), (20, 24), (60, 66)]
    char_boxes = [
        CharBox(
            char=char,
            page_no=1,
            bbox=BBox(x0=x0, y0=20, x1=x1, y1=30),
            text_index=index,
        )
        for index, (char, (x0, x1)) in enumerate(zip(text, positions, strict=True))
    ]
    clause = Clause(
        clause_id="N001",
        text=text,
        normalized_text="AB",
        char_boxes=char_boxes,
    )
    diff = DiffEngine().build_diffs([ClausePair(original=None, compare=clause)])[0]

    located = EvidenceLocator().locate([diff], [], [clause])[0]

    assert [evidence.text for evidence in located.compare_evidence] == ["A", "B"]
    assert all(evidence.highlight_type == "ADD" for evidence in located.compare_evidence)
    assert all(evidence.bbox.x1 - evidence.bbox.x0 < 10 for evidence in located.compare_evidence)


def test_highlight_rule_add_only_marks_compare_side() -> None:
    clause = clause_with_boxes("N001", "新增条款")
    diff = DiffEngine().build_diffs([ClausePair(original=None, compare=clause)])[0]

    located = EvidenceLocator().locate([diff], [], [clause])[0]

    assert located.original_evidence == []
    assert located.compare_evidence
    assert {evidence.highlight_type for evidence in located.compare_evidence} == {"ADD"}


def test_highlight_rule_delete_only_marks_original_side() -> None:
    clause = clause_with_boxes("O001", "删除条款")
    diff = DiffEngine().build_diffs([ClausePair(original=clause, compare=None)])[0]

    located = EvidenceLocator().locate([diff], [clause], [])[0]

    assert located.original_evidence
    assert {evidence.highlight_type for evidence in located.original_evidence} == {"DELETE"}
    assert located.compare_evidence == []


def test_highlight_rule_modify_marks_both_sides_yellow() -> None:
    left = clause_with_boxes("O001", "数量：100")
    right = clause_with_boxes("N001", "数量：200")
    diff = DiffEngine().build_diffs([ClausePair(original=left, compare=right)])[0]

    located = EvidenceLocator().locate([diff], [left], [right])[0]

    assert located.original_evidence
    assert located.compare_evidence
    assert {evidence.highlight_type for evidence in located.original_evidence} == {"MODIFY"}
    assert {evidence.highlight_type for evidence in located.compare_evidence} == {"MODIFY"}


def test_compare_side_add_wins_over_overlapping_modify_evidence() -> None:
    add = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=100, y0=100, x1=150, y1=120),
                text="8,000.00",
                highlight_type="ADD",
            )
        ],
    )
    modify = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=98, y0=98, x1=152, y1=122),
                text="8,000.00",
                highlight_type="MODIFY",
            )
        ],
    )

    locator = EvidenceLocator()
    locator._resolve_side_conflicts([add, modify], side="compare")

    assert add.compare_evidence
    assert add.compare_evidence[0].highlight_type == "ADD"
    assert modify.compare_evidence == []


def test_same_type_overlap_keeps_smaller_box() -> None:
    small = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=100, y0=100, x1=120, y1=120),
                text="文本",
                highlight_type="MODIFY",
            )
        ],
    )
    large = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=95, y0=95, x1=130, y1=125),
                text="文本",
                highlight_type="MODIFY",
            )
        ],
    )

    locator = EvidenceLocator()
    locator._resolve_side_conflicts([large, small], side="compare")

    assert large.compare_evidence == []
    assert small.compare_evidence


def clause_with_boxes(clause_id: str, text: str) -> Clause:
    return Clause(
        clause_id=clause_id,
        text=text,
        normalized_text=text,
        char_boxes=[
            CharBox(
                char=char,
                page_no=1,
                bbox=BBox(x0=10 + index * 8, y0=20, x1=16 + index * 8, y1=30),
                text_index=index,
            )
            for index, char in enumerate(text)
        ],
    )
