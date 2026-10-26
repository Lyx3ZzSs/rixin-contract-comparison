import json
from pathlib import Path

from app.models import Clause, CompareTask, DiffItem
from app.services.clause_rewrite_highlights import prepare_clause_highlights
from app.services.diff_events import build_snapshot, event_response


def fixture():
    data = json.loads((Path(__file__).parent / 'fixtures/financial_amount_highlights.json').read_text())
    return DiffItem.model_validate(data['diff']), {s: Clause.model_validate(c) for s, c in data['clauses'].items()}


def test_real_amount_and_ocr_edits_do_not_paint_unchanged_clause():
    diff, clauses = fixture()
    before = diff.model_dump(exclude={'clause_display_evidence', 'field_display_evidence'})
    prepare_clause_highlights([diff], [clauses['original']], [clauses['compare']])
    assert diff.clause_display_evidence is None
    display = diff.field_display_evidence
    assert display is not None
    for side, expected in (
        ('original', ['柒万捌仟元整', '陆万捌仟捌佰陆拾柒元玖角贰分']),
        ('compare', ['柒万叁任元整', '陆万捌任捌佰陆拾柒元玖角贰分']),
    ):
        boxes = getattr(display, side)
        assert [b.text for b in boxes] == expected
        assert all(b.page_no == 3 and b.highlight_type == 'MODIFY' for b in boxes)
        for box, value in zip(boxes, expected):
            start = clauses[side].text.index(value)
            chars = clauses[side].char_boxes[start:start + len(value)]
            assert box.bbox.x0 == min(c.bbox.x0 for c in chars)
            assert box.bbox.x1 == max(c.bbox.x1 for c in chars)
    assert diff.model_dump(exclude={'clause_display_evidence', 'field_display_evidence'}) == before
    task = CompareTask(task_id='amount', diffs=[diff])
    task.event_snapshot = build_snapshot(task)
    events = event_response(task)['events']
    assert len(events) == 1 and events[0]['direction'] == 'MODIFY'
    assert events[0]['diff']['field_display_evidence'] is not None


def test_untrusted_amount_geometry_falls_back_to_raw_edits_not_whole_clause():
    for failure in ('missing', 'wrong_char', 'text_mismatch'):
        diff, clauses = fixture()
        if failure == 'missing':
            clauses['compare'].char_boxes[30] = None
        elif failure == 'wrong_char':
            clauses['compare'].char_boxes[30].char = '捌'
        else:
            clauses['compare'].text += '额外内容'
        prepare_clause_highlights([diff], [clauses['original']], [clauses['compare']])
        assert diff.clause_display_evidence is None
        assert diff.field_display_evidence is None


def test_amount_classification_does_not_depend_on_quality_flags_or_title():
    diff, clauses = fixture()
    diff.review_flags = []
    diff.title = ''
    prepare_clause_highlights([diff], [clauses['original']], [clauses['compare']])
    assert diff.clause_display_evidence is None
    assert diff.field_display_evidence is not None


def test_amount_and_substantive_prose_rewrite_keeps_clause_presentation():
    from app.models import BBox, EvidenceBox
    from app.services.diff_engine import DiffEngine

    a = '4.1 合同总价为柒万捌仟元整，乙方完成全部工作并经验收后付款。'
    b = '4.1 合同总价为柒万叁仟元整，乙方完成部分工作并提供发票后付款。'
    _, _, ra, rb = DiffEngine()._changed_snippets(a, b)
    diff = DiffItem(diff_id='mixed', diff_type='MODIFY', clause_no='4.1',
                    original_clause_id='a', compare_clause_id='b', original_text=a, compare_text=b,
                    original_change_ranges=ra, compare_change_ranges=rb)
    def clause(cid, text):
        return Clause(clause_id=cid, text=text, normalized_text=text, bboxes=[
            EvidenceBox(page_no=1, bbox=BBox(x0=10, y0=10, x1=500, y1=30), text=text)])
    prepare_clause_highlights([diff], [clause('a', a)], [clause('b', b)])
    assert diff.field_display_evidence is None
    assert diff.clause_display_evidence is not None


def test_wrapped_amount_never_fills_between_lines_or_pages_and_recomputation_clears_stale_scope():
    diff, clauses = fixture()
    start = clauses['compare'].text.index('陆万')
    for char in clauses['compare'].char_boxes[start + 4:start + len('陆万捌任捌佰陆拾柒元玖角贰分')]:
        char.page_no = 4
    prepare_clause_highlights([diff], [clauses['original']], [clauses['compare']])
    boxes = diff.field_display_evidence.compare
    assert [b.page_no for b in boxes] == [3, 3, 4]
    assert ''.join(b.text for b in boxes) == '柒万叁任元整陆万捌任捌佰陆拾柒元玖角贰分'
    clauses['compare'].char_boxes[start] = None
    prepare_clause_highlights([diff], [clauses['original']], [clauses['compare']])
    assert diff.field_display_evidence is None and diff.clause_display_evidence is None
