from copy import deepcopy

import pytest

from app.models import Clause
from app.models_table import StructuredTable, TableRow, TableCell
from app.services.matcher import ClauseMatcher
from app.services.table_compare.matcher import TableMatcher
from app.services.table_compare import utils


def table(rows=8):
    return StructuredTable(page_no=1, col_count=3, rows=[
        TableRow(row_index=i, cells=[
            TableCell(row_index=i, col_index=0, text=str(i + 1)),
            TableCell(row_index=i, col_index=1, text=f"产品名称 设备{i}"),
            TableCell(row_index=i, col_index=2, text="１００ 元"),
        ]) for i in range(rows)
    ])


def test_row_alignment_computes_table_type_once_per_input(monkeypatch):
    calls = []
    raw = utils.table_type
    monkeypatch.setattr(utils, "table_type", lambda t: (calls.append(t), raw(t))[1])
    left, right = table(), table()
    before = (left.model_dump(), right.model_dump())
    assert TableMatcher().align_rows(left, right) == [(i, i) for i in range(8)]
    assert (left.model_dump(), right.model_dump()) == before
    assert len(calls) == 2


def test_row_alignment_reuses_normalized_cells(monkeypatch):
    calls = []
    raw = utils.normalize
    monkeypatch.setattr(utils, "normalize", lambda text: (calls.append(text), raw(text))[1])
    TableMatcher().align_rows(table(), table())
    # One cell normalization and one independent summary-label parse per side.
    assert calls.count("１００ 元") <= 4


def test_table_pairing_reuses_complete_text_and_small_row_features(monkeypatch):
    calls = []
    raw = utils.table_type
    monkeypatch.setattr(utils, "table_type", lambda t: (calls.append(t), raw(t))[1])
    assert TableMatcher().match_tables([table(2) for _ in range(5)], [table(2) for _ in range(5)]) == [
        (i, i, 1.0) for i in range(5)
    ]
    assert len(calls) == 10


def test_table_features_refresh_after_cell_and_structure_changes():
    matcher = TableMatcher()
    left, right = table(4), table(4)
    matcher.align_rows(left, right)
    right.rows.pop(1)
    for i, row in enumerate(right.rows):
        row.row_index = i
        for cell in row.cells:
            cell.row_index = i
    right.rows[0].cells[1].text = "联系人 张三"
    right.rows[0].cells[0].colspan = 2
    right.col_count = 4
    assert matcher.align_rows(left, right) == TableMatcher().align_rows(deepcopy(left), deepcopy(right))
    assert matcher.match_tables([left], [right]) == TableMatcher().match_tables([deepcopy(left)], [deepcopy(right)])


def test_clause_fingerprint_normalizes_unchanged_title_once_and_updates_mutations(monkeypatch):
    matcher = ClauseMatcher()
    plain = ClauseMatcher(feature_cache_enabled=False)
    clause = Clause(clause_id="same", text="付款金额100元", normalized_text="付款金额100元", title="独特标题", clause_no="1", page_numbers=[1])
    calls = []
    normalizer = matcher.alignment_analyzer.normalizer.normalize_for_match
    monkeypatch.setattr(matcher.alignment_analyzer.normalizer, "normalize_for_match",
                        lambda value: (calls.append(value), normalizer(value))[1])

    def match_probe(original, compare):
        for _ in range(8):
            assert matcher.alignment_analyzer.fingerprint(clause) == plain.alignment_analyzer.fingerprint(clause)
        assert calls.count("独特标题") == 1
        for field, value in [("text", "付款金额200元"), ("normalized_text", "付款金额300元"),
                             ("title", "修改标题"), ("clause_no", "2"),
                             ("section_type", "appendix"), ("page_numbers", [2, 3])]:
            setattr(clause, field, value)
            assert matcher.alignment_analyzer.fingerprint(clause) == plain.alignment_analyzer.fingerprint(clause)
        return []

    monkeypatch.setattr(matcher, "_match_clauses", match_probe)
    matcher.match([clause], [clause])


def test_snapshot_cell_index_preserves_overlapping_and_sparse_anchors():
    from app.services.table_compare.match_features import TableMatchSnapshot
    source = table(3)
    source.rows[0].cells[0].colspan = 2
    source.rows[1].row_index = 7
    source.rows.append(deepcopy(source.rows[0]))
    source.rows[-1].cells[0].text = "duplicate row must lose"
    snapshot = TableMatchSnapshot.from_table(source)
    for row in range(9):
        for col in range(source.col_count):
            expected = utils.anchor_cell(source, row, col)
            actual = utils.anchor_cell(snapshot, row, col)
            assert (actual.text if actual else None) == (expected.text if expected else None)


def test_cached_table_matching_scores_and_dp_equal_uncached_path(monkeypatch):
    from app.services.table_compare.match_features import TableMatchSnapshot
    import random
    rng = random.Random(17)
    for _ in range(12):
        original = [table(rng.randint(1, 7)) for _ in range(rng.randint(1, 4))]
        compare = deepcopy(original)
        for item in compare:
            for row in item.rows:
                if rng.random() < 0.4:
                    row.cells[1].text = rng.choice(["联系人 张三", "验收指标", "付款10元", "", "合计100元"])
                if rng.random() < 0.2:
                    row.cells[0].colspan = 2
        fast = TableMatcher()
        actual_tables = fast.match_tables(original, compare)
        actual_rows = [fast.align_rows(left, right) for left in original for right in compare]
        with monkeypatch.context() as patch:
            patch.setattr(TableMatchSnapshot, "from_table", classmethod(lambda cls, source: source))
            plain = TableMatcher()
            assert actual_tables == plain.match_tables(original, compare)
            assert actual_rows == [plain.align_rows(left, right) for left in original for right in compare]


def test_cached_fingerprints_preserve_full_clause_pairs():
    clauses = [Clause(clause_id=f"left-{i}", clause_no=str(i), title=f"服务{i}",
                      text=f"服务费用{i + 100}元，付款期限30天。", normalized_text=f"服务费用{i + 100}元，付款期限30天。",
                      section_path=["主合同"], page_numbers=[1]) for i in range(12)]
    compare = [clause.model_copy(deep=True, update={"clause_id": f"right-{i}"}) for i, clause in enumerate(clauses)]
    compare[3].text = compare[3].normalized_text = "服务费用200元，付款期限60天。"
    fast = ClauseMatcher(feature_cache_enabled=True).match(clauses, compare)
    plain = ClauseMatcher(feature_cache_enabled=False).match(clauses, compare)
    assert [pair.model_dump() for pair in fast] == [pair.model_dump() for pair in plain]


def test_fingerprint_cache_clears_on_error(monkeypatch):
    matcher = ClauseMatcher()
    original = matcher.alignment_analyzer._fingerprint_from_parts
    clause = Clause(clause_id="c", title="付款", text="100元", normalized_text="100元")
    def fail(left, right):
        matcher.alignment_analyzer.fingerprint(clause)
        raise RuntimeError("abort")
    monkeypatch.setattr(matcher, "_match_clauses", fail)
    with pytest.raises(RuntimeError, match="abort"):
        matcher.match([clause], [clause])
    assert matcher.alignment_analyzer._fingerprint_from_parts == original
    clause.text = "200元"
    assert matcher.alignment_analyzer.fingerprint(clause) == ClauseMatcher().alignment_analyzer.fingerprint(clause)
