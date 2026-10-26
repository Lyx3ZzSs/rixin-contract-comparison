from concurrent.futures import ThreadPoolExecutor
import importlib
import threading
from types import SimpleNamespace

import httpx
import pytest


def api():
    from app.services import pipeline_metrics
    assert hasattr(pipeline_metrics.StageMetrics, '__dataclass_fields__')
    assert 'stage_id' in pipeline_metrics.StageMetrics.__dataclass_fields__
    return importlib.import_module('app.services.task_observability')


def test_scope_thread_and_task_isolation_and_queue():
    obs = api()
    barrier = threading.Barrier(2)
    def task(task_id):
        with obs.task_observation(task_id) as scope:
            with obs.stage_observation('same_name_1'):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    def request(_):
                        barrier.wait(timeout=5)
                        with obs.request_attempt('roi_ocr', input_count=1):
                            assert obs.current_observation() is scope
                    list(obs.map_observed(pool, request, range(2)))
            snapshot = scope.snapshot()
        assert obs.current_observation() is None
        return snapshot
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(task, ['a', 'b']))
    for task_id, result in zip(['a', 'b'], results):
        assert result['task_id'] == task_id
        assert len(result['requests']) == 2
        assert all(r['stage_id'] == 'same_name_1' for r in result['requests'])
        assert all(r['task_id'] == task_id for r in result['requests'])
        assert len(result['waits']) == 2
        assert all(w['duration_seconds'] >= 0 for w in result['waits'])


def test_embedding_retry_preserves_body_failure_and_no_network():
    obs = api()
    from app.services.matching.providers import SemanticMatcher
    matcher = SemanticMatcher(enabled=True, provider='openai', base_url='https://model.invalid/v1', model='test', max_retries=1)
    bodies = []
    def post(url, **kwargs):
        bodies.append(kwargs['json'])
        if len(bodies) == 1:
            raise httpx.ConnectError('offline fixture')
        return httpx.Response(200, request=httpx.Request('POST', url), json={'data': [{'index': 0, 'embedding': [1.0, 0.0]}]})
    matcher._http_client = SimpleNamespace(post=post)
    with obs.task_observation('retry') as scope, obs.stage_observation('match'):
        assert matcher._post_openai_embeddings(['a']) == [[1.0, 0.0]]
    rows = scope.snapshot()['requests']
    assert [r['attempt'] for r in rows] == [1, 2]
    assert [r['retry'] for r in rows] == [False, True]
    assert [r['outcome'] for r in rows] == ['error', 'success']
    assert bodies == [{'model': 'test', 'input': ['a']}] * 2
    assert all(r['kind'] == 'embedding' and r['wall_seconds'] >= 0 for r in rows)


def test_roi_request_is_in_ledger_without_network(monkeypatch):
    obs = api()
    from app.services.seal_ocr import SealOCRService
    from app.config import settings
    monkeypatch.setattr(settings, 'ppocrv5_url', 'https://ocr.invalid')
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs['json'])
        return httpx.Response(200, request=httpx.Request('POST', url), json={'result': {'ocrResults': []}})
    service = SealOCRService(client_provider=SimpleNamespace(get_ocr_client=lambda: SimpleNamespace(post=post)))
    with obs.task_observation('roi') as scope:
        assert service._call_ocr_api(SimpleNamespace(tobytes=lambda fmt: b'png')) == ''
    assert len(calls) == 1
    assert scope.snapshot()['requests'][0]['kind'] == 'roi_ocr'
    assert scope.snapshot()['requests'][0]['cache_hit'] is False


def test_duplicate_display_names_have_stable_unique_ids():
    obs = api()
    class FirstStage:
        name = '相同展示名称'
    class SecondStage:
        name = '相同展示名称'
    assert obs.stage_ids([FirstStage(), SecondStage(), FirstStage()]) == ['first', 'second', 'first_2']


def test_cache_hits_are_distinct_from_http_attempts():
    obs = api()
    from app.services.matching.providers import SemanticMatcher
    matcher = SemanticMatcher(enabled=True, provider='openai', base_url='https://model.invalid/v1', model='test')
    matcher._cache['cached'] = [1.0]
    with obs.task_observation('cache') as scope:
        assert matcher._embeddings(['cached', 'cached']) == [[1.0], [1.0]]
    snapshot = scope.snapshot()
    assert snapshot['totals']['http_attempt_count'] == 0
    assert snapshot['totals']['cache_hit_input_count'] == 2


def test_response_server_queue_and_input_bytes_are_explicit():
    obs = api()
    with obs.task_observation('response') as scope:
        with obs.request_attempt('full_ocr', input_bytes=20) as row:
            response = httpx.Response(200, headers={'Server-Timing': 'queue;dur=12.5, infer;dur=35'})
            obs.observe_response(row, response, {})
    row = scope.snapshot()['requests'][0]
    assert row['input_bytes'] == 20
    assert row['server_timing_seconds']['queue'] == 0.0125
    assert row['server_queue_seconds'] == 0.0125


def test_pipeline_records_duplicate_names_cpu_and_request_attribution(tmp_path, monkeypatch):
    obs = api()
    import app.services.pipeline as pipeline
    from app.models import CompareTask, CompareOptions
    monkeypatch.setattr(pipeline, '_update_progress', lambda *args: None)
    class Repo:
        def load_compare_task(self, task_id):
            raise FileNotFoundError(task_id)
    class Stage:
        name, start_progress, progress = '同名', 1, 2
        def execute(self, ctx):
            with obs.request_attempt('roi_ocr'):
                pass
    task = CompareTask(task_id='timings', original_filename='a.pdf', compare_filename='b.pdf', options=CompareOptions())
    ctx = pipeline.PipelineContext(task=task, original_pdf=tmp_path/'a.pdf', compare_pdf=tmp_path/'b.pdf')
    pipeline.ComparePipeline(stages=[Stage(), Stage()], repository=Repo()).run(ctx)
    rows = task.metrics['stages']
    assert [row['stage_id'] for row in rows] == ['stage', 'stage_2']
    assert all(row['cpu_thread_seconds'] >= 0 and row['cpu_process_seconds'] >= 0 for row in rows)
    assert [row['stage_id'] for row in task.metrics['performance']['request_ledger']['requests']] == ['stage', 'stage_2']


def test_pipeline_failure_persists_ledger_and_clears_context(tmp_path, monkeypatch):
    obs = api()
    import app.services.pipeline as pipeline
    from app.models import CompareTask, CompareOptions
    monkeypatch.setattr(pipeline, '_update_progress', lambda *args: None)
    class FailingStage:
        name, start_progress, progress = 'failure', 1, 2
        def execute(self, ctx):
            with obs.request_attempt('roi_ocr'):
                raise ValueError('same failure')
    task = CompareTask(task_id='failed', original_filename='a.pdf', compare_filename='b.pdf', options=CompareOptions())
    ctx = pipeline.PipelineContext(task=task, original_pdf=tmp_path/'a.pdf', compare_pdf=tmp_path/'b.pdf')
    with pytest.raises(ValueError, match='same failure'):
        pipeline.ComparePipeline(stages=[FailingStage()]).run(ctx)
    assert obs.current_observation() is None
    assert task.metrics['stages'][0]['error'] == 'same failure'
    assert task.metrics['performance']['request_ledger']['requests'][0]['outcome'] == 'error'


@pytest.mark.parametrize('mode', ['http_error', 'invalid_json', 'service_error'])
def test_roi_error_semantics_are_preserved_and_observed(monkeypatch, mode):
    obs = api()
    from app.services.seal_ocr import SealOCRService
    from app.config import settings
    monkeypatch.setattr(settings, 'ppocrv5_url', 'https://ocr.invalid')
    responses = {
        'http_error': httpx.Response(503, request=httpx.Request('POST', 'https://ocr.invalid')),
        'invalid_json': httpx.Response(200, content=b'not json', request=httpx.Request('POST', 'https://ocr.invalid')),
        'service_error': httpx.Response(200, json={'errorCode': 3}, request=httpx.Request('POST', 'https://ocr.invalid')),
    }
    client = SimpleNamespace(post=lambda *a, **kw: responses[mode])
    service = SealOCRService(client_provider=SimpleNamespace(get_ocr_client=lambda: client))
    with obs.task_observation('errors') as scope:
        assert service._call_ocr_api(SimpleNamespace(tobytes=lambda fmt: b'png')) == ''
    assert scope.snapshot()['requests'][0]['outcome'] == 'error'


def test_many_cache_hits_are_aggregated_without_request_rows():
    obs = api()
    with obs.task_observation('cached') as scope, obs.stage_observation('match'):
        for _ in range(100):
            with obs.request_attempt('embedding', cache_hit=True, input_count=2):
                pass
    result = scope.snapshot()
    assert result['requests'] == []
    assert result['cache_hits'][0]['event_count'] == 100
    assert result['cache_hits'][0]['input_count'] == 200


def test_explicit_stage_ids_cannot_collide_with_generated_suffixes():
    obs = api()
    class Stage:
        def __init__(self, stage_id):
            self.stage_id = stage_id
    ids = obs.stage_ids([Stage('first'), Stage('first'), Stage('first_2')])
    assert len(set(ids)) == 3


def test_http_exception_keeps_status_without_changing_exception():
    obs = api()
    with obs.task_observation('status') as scope:
        with pytest.raises(httpx.HTTPStatusError):
            with obs.request_attempt('full_ocr'):
                httpx.Response(503, request=httpx.Request('POST', 'https://ocr.invalid')).raise_for_status()
    assert scope.snapshot()['requests'][0]['status_code'] == 503
