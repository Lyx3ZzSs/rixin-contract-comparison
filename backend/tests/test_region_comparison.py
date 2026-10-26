"""Captured native/scan forms: ownership must not erase real changes."""
import json
from pathlib import Path

import pytest

from app.models import Document
from app.services.clause_splitter import ClauseSplitter
from app.services.table_compare import TableComparator


@pytest.fixture
def forms():
    data = json.loads((Path(__file__).parent / 'fixtures/region_comparison/huabei_forms.json').read_text())
    return [Document.model_validate(data[s]) for s in ('original', 'compare')]


def compare_and_split(forms):
    comparator = TableComparator()
    diffs, _ = comparator.build_diffs(*forms)
    ownership = comparator.region_result
    units = []
    for side, doc in zip(('original', 'compare'), forms):
        splitter = ClauseSplitter()
        splitter.region_result = ownership
        splitter.comparison_side = side
        units.append(splitter._collect_units(doc))
    return comparator, diffs, units


def test_cross_page_field_is_compared_as_complete_content(forms):
    _, diffs, _ = compare_and_split(forms)
    assert not [d for d in diffs if '同意送审计' in d.original_text or '同意送审计' in d.compare_text]


@pytest.mark.parametrize('page,token', [(15, '合盛堡'), (26, '有限公司'), (27, '建设单位'), (28, '承包单位')])
def test_representation_drift_does_not_leave_one_sided_clause_content(forms, page, token):
    _, diffs, units = compare_and_split(forms)
    present = [any(u.page_no == page and token in u.text for u in group) for group in units]
    assert present[0] == present[1], present
    assert not [d for d in diffs if d.diff_type in {'DELETE', 'ADD'}
                and token in d.original_snippet + d.compare_snippet
                and any(e.page_no == page for e in d.original_evidence + d.compare_evidence)]


def test_real_filled_date_survives_continuation(forms):
    for block in forms[1].pages[-2].blocks:
        if block.raw_html:
            pos = block.raw_html.rfind('年月日')
            block.raw_html = block.raw_html[:pos] + block.raw_html[pos:].replace('年月日', '2026年10月20日', 1)
    _, diffs, _ = compare_and_split(forms)
    assert any('2026年10月20日' in d.compare_text for d in diffs)


@pytest.mark.parametrize('fragment', ['请审核', '乙方单位负责人：'])
def test_real_region_deletion_is_not_lost(forms, fragment):
    page = next(p for p in forms[1].pages if p.page_no == 26)
    for block in page.blocks:
        block.text = block.text.replace(fragment, '')
    _, diffs, _ = compare_and_split(forms)
    assert any(fragment.replace('：', ':') in d.original_snippet for d in diffs)


def test_real_region_amount_change_is_not_lost(forms):
    for side, doc in enumerate(forms):
        for page in doc.pages:
            if page.page_no == 26:
                for block in page.blocks:
                    block.text = block.text.replace('**费用', f'{1000 if side == 0 else 2000}费用')
    _, diffs, _ = compare_and_split(forms)
    assert any('1' in d.original_snippet and '2' in d.compare_snippet for d in diffs)


@pytest.mark.parametrize('page,label,value', [(27, '建设单位', '甲公司'), (28, '合同号', 'HT2026')])
def test_plain_field_value_change_is_compared_even_when_label_is_equal(forms, page, label, value):
    target_side = 0 if page == 27 else 1
    for p in forms[target_side].pages:
        if p.page_no == page:
            for block in p.blocks:
                if block.text == label:
                    block.text += value
    _, diffs, _ = compare_and_split(forms)
    assert any(value in d.original_snippet + d.compare_snippet for d in diffs)


def test_real_caption_change_is_retained(forms):
    page = next(p for p in forms[1].pages if p.page_no == 15)
    block = next(b for b in page.blocks if b.block_role == 'table_caption')
    block.text = block.text.replace('合盛堡', '新场站')
    _, diffs, _ = compare_and_split(forms)
    assert any('新场站' in d.compare_snippet for d in diffs)


def test_missing_region_correspondence_is_an_observation(forms):
    page = next(p for p in forms[0].pages if p.page_no == 26)
    page.blocks = []
    comparator, _, _ = compare_and_split(forms)
    assert any(o.reason == 'REGION_ALIGNMENT_UNCERTAIN' for o in comparator.region_result.observations)


def test_comparison_does_not_change_extracted_documents(forms):
    before = [d.model_dump_json() for d in forms]
    compare_and_split(forms)
    assert before == [d.model_dump_json() for d in forms]


def test_continuation_evidence_retains_original_page(forms):
    page = next(p for p in forms[0].pages if p.page_no == 30)
    for block in page.blocks:
        if block.raw_html:
            block.raw_html = block.raw_html.replace('年月日', '2025年12月31日')
    _, diffs, _ = compare_and_split(forms)
    changed = next(d for d in diffs if '2025年12月31日' in d.original_text)
    assert {e.page_no for e in changed.original_evidence} == {30}
    assert {e.page_no for e in changed.compare_evidence} == {29}


def test_repeated_labels_elsewhere_do_not_claim_a_missing_row(forms):
    page = next(p for p in forms[1].pages if p.page_no == 28)
    for block in page.blocks:
        if block.block_type == 'text':
            block.bbox.y0 += 400
            block.bbox.y1 += 400
    comparator, diffs, _ = compare_and_split(forms)
    assert not [d for d in comparator.region_result.decisions if d['reason'] == 'row_representation'
                and any('p28:' in bid for bid in d['original_blocks'])]
    assert any('承包单位合同号建设单位编号' in d.original_text for d in diffs)


def test_ambiguous_nearby_rows_do_not_establish_field_identity(forms):
    page = next(p for p in forms[1].pages if p.page_no == 28)
    duplicates = []
    for b in page.blocks:
        if b.block_type == 'text':
            copy = b.model_copy(deep=True)
            copy.block_id += '-repeat'
            copy.bbox.y0 += 24
            copy.bbox.y1 += 24
            duplicates.append(copy)
    page.blocks.extend(duplicates)
    comparator, _, _ = compare_and_split(forms)
    assert not any(comparator.region_result.claims('compare', b) for b in duplicates)


def test_low_confidence_region_change_remains_reviewable(forms):
    page = next(p for p in forms[1].pages if p.page_no == 26)
    block = next(b for b in page.blocks if '请审核' in b.text)
    block.text = block.text.replace('请审核', '请签字')
    block.confidence = .6
    _, diffs, _ = compare_and_split(forms)
    assert any(d.match_score_details['change_decision'] == 'NEEDS_REVIEW' for d in diffs if '签字' in d.compare_snippet)


def test_uncertain_punctuation_becomes_an_observation(forms):
    from app.models import CompareTask
    from app.services.comparison_observations import separate_observations
    _, diffs, _ = compare_and_split(forms)
    task = CompareTask(task_id='region-test', diffs=diffs)
    separate_observations(task)
    assert not any(d.original_snippet == '。' for d in task.diffs)
    assert any(o.reason == 'REGION_READING_UNCERTAIN' for o in task.comparison_observations)


def test_unpaired_caption_is_not_silently_excluded(forms):
    page = next(p for p in forms[1].pages if p.page_no == 15)
    block = next(b for b in page.blocks if b.block_role == 'table_caption')
    block.bbox.y0 += 100
    block.bbox.y1 += 100
    _, _, units = compare_and_split(forms)
    assert any(u.block_id == block.block_id for u in units[1])


def test_event_projection_preserves_changed_continuation_page(forms):
    from app.models import CompareTask
    from app.services.diff_events import event_response
    page = next(p for p in forms[0].pages if p.page_no == 30)
    for block in page.blocks:
        if block.raw_html:
            block.raw_html = block.raw_html.replace('年月日', '2025年12月31日')
    _, diffs, _ = compare_and_split(forms)
    changed = next(d for d in diffs if '2025年12月31日' in d.original_text)
    response = event_response(CompareTask(task_id='cross-page', diffs=[changed]))
    projected = response['events'][0]['diff']
    assert {e['page_no'] for e in projected['original_evidence']} == {30}
    assert {e['page_no'] for e in projected['compare_evidence']} == {29}


def test_reverse_comparison_retains_the_same_ownership(forms):
    _, diffs, units = compare_and_split(list(reversed(forms)))
    assert not any('同意送审计' in d.original_snippet + d.compare_snippet for d in diffs)
    for page, token in [(15, '合盛堡'), (26, '有限公司'), (27, '建设单位'), (28, '承包单位')]:
        assert [any(u.page_no == page and token in u.text for u in group) for group in units] == [False, False]


def test_occluded_row_is_not_a_confirmed_deletion():
    data = json.loads((Path(__file__).parent / 'fixtures/region_comparison/occluded_signing_row.json').read_text())
    documents = [Document.model_validate(data[s]) for s in ('original', 'compare')]
    comparator, _, _ = compare_and_split(documents)
    candidates = [d for d in comparator.region_result.diffs if '授权代表' in d.original_snippet]
    assert candidates
    assert all(d.match_score_details['change_decision'] == 'NEEDS_REVIEW' for d in candidates)
