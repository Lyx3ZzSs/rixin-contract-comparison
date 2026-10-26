from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models import CompareTask, DiffItem
from app.services.pipeline import PipelineContext
from app.services.pipeline_stages import PageAlignmentStage, SummaryStage
from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus
from app.services.task_observability import task_observation


def context():
    return PipelineContext(CompareTask(task_id='overlap'), Path('left.pdf'), Path('right.pdf'))


def test_starts_after_frozen_alignment_and_closes_on_later_failure(monkeypatch):
    import app.services.matrix_job as module
    import app.services.pipeline_stages as stages
    events = []
    aligned = PageAlignmentResult((PageAlignmentEntry(PageAlignmentStatus.MATCHED, (1,), (1,), 1.0, (), 'test'),))
    ctx = context()
    ctx.original_extraction = ctx.compare_extraction = SimpleNamespace(document=object())
    stage = PageAlignmentStage()
    stage.aligner = SimpleNamespace(align=lambda *args: (events.append('aligned'), aligned)[1])
    job = SimpleNamespace(close=lambda: events.append('close'))
    def start(left, right, alignment, budget):
        assert ctx.page_alignment is aligned
        assert alignment.entries is aligned.entries
        assert alignment.performance == {}
        events.append('start')
        return job
    monkeypatch.setattr(settings, 'compare_matrix_background_enabled', True)
    monkeypatch.setattr(module.MatrixScanJob, 'start', start)
    monkeypatch.setattr(stages, '_write_debug_artifact', lambda *args, **kwargs: None)
    monkeypatch.setattr(stages, '_emit_progress', lambda *args: None)
    with pytest.raises(RuntimeError, match='later stage'):
        with task_observation(ctx.task.task_id):
            stage.execute(ctx)
            assert ctx.matrix_scan_job is job
            assert events == ['aligned', 'start']
            raise RuntimeError('later stage')
    assert events == ['aligned', 'start', 'close']


def test_summary_keeps_original_graphic_application_order(monkeypatch):
    import app.services.pipeline_stages as stages
    ctx = context()
    normal = DiffItem(diff_id='D001', diff_type='ADD', title='normal')
    graphic = DiffItem(diff_id='GRAPHIC001', diff_type='ADD', title='graphic')
    ctx.diffs = [normal]
    calls = []
    def result(cancel):
        cancel()
        calls.append('await')
        return ([graphic], [{'decision': 'ABSENT'}]), {'execution': 'background_process'}
    ctx.matrix_scan_job = SimpleNamespace(result=result, close=lambda: calls.append('close'))
    def reconcile(diffs):
        assert diffs == [normal, graphic]
        calls.append('reconcile')
        return diffs, {}
    monkeypatch.setattr(stages, '_reconcile_visual_signing_date_diffs', reconcile)
    monkeypatch.setattr(stages, '_write_debug_artifact', lambda *args, **kwargs: None)
    monkeypatch.setattr(stages, '_emit_progress', lambda *args: None)
    SummaryStage().execute(ctx)
    assert calls == ['await', 'close', 'reconcile']
    assert ctx.task.diffs == [normal, graphic]
    assert ctx.task.metrics['graphic_observations'] == [{'decision': 'ABSENT'}]


def test_summary_preserves_exception_and_closes_job(monkeypatch):
    ctx = context()
    calls = []
    failure = ValueError('matrix failure')
    def result(cancel):
        raise failure
    ctx.matrix_scan_job = SimpleNamespace(result=result, close=lambda: calls.append('close'))
    with pytest.raises(ValueError) as caught:
        SummaryStage().execute(ctx)
    assert caught.value is failure
    assert calls == ['close']
    assert ctx.task.diffs == []


def test_cancel_after_alignment_reaps_ready_job_without_applying_graphics(tmp_path, monkeypatch):
    import multiprocessing
    import fitz
    from app.errors import TaskCancelled
    import app.services.pipeline as pipeline_module
    import app.services.pipeline_stages as stages
    path = tmp_path / 'page.pdf'
    with fitz.open() as document:
        document.new_page(width=100, height=100)
        document.save(path)
    ctx = PipelineContext(CompareTask(task_id='cancel-overlap'), path, path)
    ctx.original_extraction = ctx.compare_extraction = SimpleNamespace(document=object())
    aligned = PageAlignmentResult((PageAlignmentEntry(PageAlignmentStatus.MATCHED, (1,), (1,), 1.0, (), 'test'),))
    stage = PageAlignmentStage()
    stage.aligner = SimpleNamespace(align=lambda *args: aligned)
    state = {'cancelled': False}
    failure = TaskCancelled('cancel after alignment')
    def check():
        if state['cancelled']:
            raise failure
    ctx.execution_context = SimpleNamespace(cancellation_token=SimpleNamespace(raise_if_cancelled=check))
    class CancelStage:
        name = 'cancel'
        start_progress = 40
        progress = 41
        def execute(self, ctx):
            assert ctx.matrix_scan_job is not None
            assert ctx.matrix_scan_job._ready.wait(5)
            state['pid'] = ctx.matrix_scan_job.process.pid
            state['cancelled'] = True
    monkeypatch.setattr(settings, 'compare_matrix_background_enabled', True)
    monkeypatch.setattr(stages, '_write_debug_artifact', lambda *args, **kwargs: None)
    monkeypatch.setattr(stages, '_emit_progress', lambda *args: None)
    monkeypatch.setattr(pipeline_module, '_update_progress', lambda *args: None)
    with pytest.raises(TaskCancelled) as caught:
        pipeline_module.ComparePipeline(stages=[stage, CancelStage(), SummaryStage()]).run(ctx)
    assert caught.value is failure
    assert ctx.diffs == []
    assert ctx.task.diffs == []
    assert 'graphic_observations' not in ctx.task.metrics
    assert state['pid'] not in {p.pid for p in multiprocessing.active_children()}
    assert not ctx.matrix_scan_job._receiver.is_alive()
    assert ctx.matrix_scan_job._closed
