"""Failure contracts: incomplete topology must not become a content change."""
from app.models import BBox, CompareTask, DiffItem, EvidenceBox
from app.services.diff_events import event_response
from app.services.table_compare.diff_builder import TableDiffBuilder
from app.models import TextBlock


def unresolved():
    box = EvidenceBox(page_no=1, bbox=BBox(x0=1,y0=2,x1=100,y1=50),method='table_cell',highlight_type='MODIFY')
    return DiffItem(diff_id='D005',diff_type='MODIFY',source_type='table',title='表格结构待复核',
                    original_evidence=[box,box.model_copy()],compare_evidence=[box.model_copy()],
                    match_score_details={'decision_reason':'TABLE_STRUCTURE_UNRESOLVED'})


def test_legacy_unresolved_table_is_observation_not_change():
    task=CompareTask(task_id='t',diffs=[unresolved()])
    response=event_response(task)
    assert response['event_count']==0
    assert response['comparison_observation_count']==1
    obs=response['comparison_observations'][0]
    assert len(obs['original_evidence'])==1
    assert obs['original_evidence'][0]['highlight_type'] is None
    assert task.diffs[0].diff_type=='MODIFY'  # read projection does not rewrite history


def test_whole_table_evidence_is_one_context_per_region():
    box=BBox(x0=0,y0=0,x1=100,y1=80)
    blocks=[(TextBlock(block_id=str(i),page_no=1,text=str(i),bbox=BBox(x0=i,y0=10,x1=i+1,y1=12),layout_bbox=box),str(i)) for i in range(47)]
    # These are 47 OCR fragments of one table, not 47 distinct cell changes.
    b=TableDiffBuilder.__new__(TableDiffBuilder)
    assert len(b._evidence_from_blocks(blocks,'MODIFY'))==1


def test_observation_persists_and_retains_review_without_changing_other_diffs():
    from app.models import AuditItemReview
    from app.services.comparison_observations import separate_observations
    task = CompareTask(task_id='t', diffs=[unresolved(), DiffItem(diff_id='D006', diff_type='MODIFY', source_type='table', original_text='152300', compare_text='152301')])
    task.event_reviews['observation-D005'] = AuditItemReview(review_status='CONFIRMED', review_comment='已对照原件')
    separate_observations(task)
    reloaded = CompareTask.model_validate_json(task.model_dump_json())
    response = event_response(reloaded)
    assert len(reloaded.diffs) == response['event_count'] == 1
    assert response['events'][0]['diff']['compare_text'] == '152301'
    assert response['comparison_observations'][0]['review_comment'] == '已对照原件'
    assert response['comparison_observations'][0]['review_status'] == 'CONFIRMED'


def test_legacy_event_review_survives_observation_projection():
    from app.models import AuditItemReview
    from app.services.diff_events import build_snapshot
    task = CompareTask(task_id='t', diffs=[unresolved()])
    task.event_snapshot = build_snapshot(task)
    event_id = task.event_snapshot['events'][0]['event_id']
    task.event_reviews[event_id] = AuditItemReview(review_status='IGNORED', review_comment='旧审核')
    result = event_response(task)['comparison_observations'][0]
    assert result['review_comment'] == '旧审核' and result['review_status'] == 'IGNORED'


def test_report_lists_structural_uncertainty_outside_changes(tmp_path):
    import fitz
    from app.services.report_generator import ReportGenerator, _visible_audit_items
    task = CompareTask(task_id='t', diffs=[unresolved()])
    assert not _visible_audit_items(task)
    for event_view in (False, True):
        path = tmp_path / f'{event_view}.pdf'
        ReportGenerator().generate(task, path, event_view=event_view)
        with fitz.open(path) as pdf:
            text = ''.join(p.get_text() for p in pdf)
        assert '表格待复核' in text and '不计入差异数' in text


def test_successful_rerun_clears_prior_structure_observation():
    from app.services.comparison_observations import separate_observations
    task = CompareTask(task_id='t', diffs=[unresolved()])
    separate_observations(task)
    assert len(task.comparison_observations) == 1
    task.diffs = []  # a subsequent successful comparison found no changes
    separate_observations(task)
    assert not task.comparison_observations


def test_legacy_list_detail_and_event_counts_agree_without_rewriting_record(tmp_path):
    import json
    from app.config import Settings
    from app.infrastructure.task_repository import SQLiteTaskRepository
    from app.api_presenters import event_summary
    from app.services.diff_events import build_snapshot
    repo = SQLiteTaskRepository(Settings(storage_dir=tmp_path))
    task = CompareTask(task_id='t', diffs=[unresolved()])
    task.event_snapshot = build_snapshot(task)
    repo.save_compare_task(task)
    # A row written by the old release has a cached total that included failures.
    db = repo._connect()
    raw = json.loads(db.execute('SELECT details_json FROM tasks WHERE task_id=?', ('t',)).fetchone()[0])
    raw['event_summary'] = {'event_count':1, 'event_version':task.event_snapshot['version']}
    encoded = json.dumps(raw)
    db.execute('UPDATE tasks SET details_json=? WHERE task_id=?', (encoded, 't'))
    db.commit()
    assert repo.list_compare_record_summaries()[0]['event_count'] == 0
    assert event_summary(repo.load_compare_task('t'))['event_count'] == 0
    assert event_response(repo.load_compare_task('t'))['event_count'] == 0
    assert db.execute('SELECT details_json FROM tasks WHERE task_id=?', ('t',)).fetchone()[0] == encoded
    repo.close()
