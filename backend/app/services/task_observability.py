"""Task-local observation only: never owns clients, retries, or request payloads.

Wall durations overlap across workers. Request wall includes response parsing;
executor and semaphore waits are separate observations, not server queue time.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
import re
import copy
import threading
import time
from typing import Any, Callable


@dataclass
class TaskObservation:
    task_id: str
    inputs: object | None = None
    cleanup_callbacks: list[Callable[[], None]] = field(default_factory=list)
    _requests: list[dict[str, Any]] = field(default_factory=list)
    _waits: list[dict[str, Any]] = field(default_factory=list)
    _cache_hits: dict[tuple[str | None, str], dict[str, Any]] = field(default_factory=dict)
    _lock: Any = field(default_factory=threading.Lock)

    def add(self, collection: str, row: dict[str, Any]) -> None:
        with self._lock:
            getattr(self, collection).append(dict(row))

    def cache_hit(self, row: dict[str, Any]) -> None:
        with self._lock:
            key = (row['stage_id'], row['kind'])
            aggregate = self._cache_hits.setdefault(key, {
                'task_id': self.task_id, 'stage_id': row['stage_id'], 'kind': row['kind'],
                'event_count': 0, 'input_count': 0, 'wall_seconds': 0.0,
            })
            aggregate['event_count'] += 1
            aggregate['input_count'] += row['input_count']
            aggregate['wall_seconds'] += row['wall_seconds']

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests = copy.deepcopy(self._requests)
            waits = copy.deepcopy(self._waits)
            cache_hits = copy.deepcopy(list(self._cache_hits.values()))
        totals = {
            'http_attempt_count': sum(not r['cache_hit'] for r in requests),
            'retry_count': sum(r['retry'] for r in requests),
            'cache_hit_input_count': sum(r['input_count'] for r in cache_hits),
            'failure_count': sum(r['outcome'] == 'error' for r in requests),
            'client_wall_seconds_sum': sum(r['wall_seconds'] for r in requests if not r['cache_hit']),
        }
        input_cache = self.inputs.snapshot() if self.inputs is not None and hasattr(self.inputs, 'snapshot') else None
        return {**({'input_cache': input_cache} if input_cache is not None else {}),
                'task_id': self.task_id, 'requests': requests, 'cache_hits': cache_hits, 'waits': waits, 'totals': totals,
                'wall_semantics': 'client_attempt_including_response_parse; concurrent_durations_overlap',
                'queue_semantics': 'executor/semaphore waits are separate; unavailable server queues are null'}


_task: ContextVar[TaskObservation | None] = ContextVar('task_observation', default=None)
_stage: ContextVar[str | None] = ContextVar('stage_observation', default=None)


def current_observation() -> TaskObservation | None:
    return _task.get()


@contextmanager
def task_observation(task_id: str):
    scope = TaskObservation(task_id)
    token = _task.set(scope)
    try:
        yield scope
    finally:
        try:
            for cleanup in reversed(scope.cleanup_callbacks):
                cleanup()
        finally:
            scope.inputs = None
            _task.reset(token)


@contextmanager
def stage_observation(stage_id: str):
    token = _stage.set(stage_id)
    try:
        yield
    finally:
        _stage.reset(token)


def stage_ids(stages) -> list[str]:
    counts: dict[str, int] = {}
    ids = []
    for stage in stages:
        name = getattr(stage, 'stage_id', None) or re.sub(r'(?<!^)(?=[A-Z])', '_', type(stage).__name__.removesuffix('Stage')).lower()
        name = name or 'stage'
        counts[name] = counts.get(name, 0) + 1
        candidate = name if counts[name] == 1 else f'{name}_{counts[name]}'
        while candidate in ids:
            counts[name] += 1
            candidate = f'{name}_{counts[name]}'
        ids.append(candidate)
    return ids


@contextmanager
def request_attempt(kind: str, *, attempt: int = 1, input_count: int = 1,
                    input_bytes: int | None = None, cache_hit: bool = False):
    scope = _task.get()
    row = {'task_id': scope.task_id if scope else None, 'stage_id': _stage.get(),
           'kind': kind, 'attempt': attempt, 'retry': attempt > 1, 'input_count': input_count,
           'input_bytes': input_bytes, 'cache_hit': cache_hit, 'outcome': 'success',
           'queue_seconds': None, 'server_queue_seconds': None}
    started = time.perf_counter()
    try:
        yield row
    except BaseException as exc:
        row['outcome'] = 'error'
        row['error_type'] = type(exc).__name__
        response = getattr(exc, 'response', None)
        if response is not None:
            observe_response(row, response, None)
        raise
    finally:
        row['wall_seconds'] = max(0.0, time.perf_counter() - started)
        if scope is not None:
            if cache_hit:
                scope.cache_hit(row)
            else:
                scope.add('_requests', row)


def observe_response(row: dict[str, Any], response, payload) -> None:
    try:
        _observe_response(row, response, payload)
    except Exception:
        # Diagnostics must not create a new failure or change retry semantics.
        pass


def _observe_response(row: dict[str, Any], response, payload) -> None:
    # Reuse the already bounded, numeric-only Server-Timing parser.
    from app.services.pipeline_metrics import PerformanceRecorder
    from app.services.remote_metrics import record_remote_metrics
    recorder = PerformanceRecorder()
    record_remote_metrics(recorder, getattr(response, 'headers', {}), payload)
    operations = recorder.snapshot()['operations']
    timing = {name.removeprefix('remote_reported_'): metric['duration_seconds']
              for name, metric in operations.items()}
    row['server_timing_seconds'] = timing
    row['server_queue_seconds'] = timing.get('queue')
    try:
        row['request_bytes'] = len(response.request.content)
    except (AttributeError, RuntimeError):
        row['request_bytes'] = None
    row['status_code'] = getattr(response, 'status_code', None)
    row['response_bytes'] = len(getattr(response, 'content', b''))
    if isinstance(payload, dict) and payload.get('errorCode') not in (0, None):
        row['outcome'] = 'error'
        row['error_type'] = 'RemoteServiceError'


def record_wait(kind: str, duration_seconds: float) -> None:
    scope = _task.get()
    if scope is not None:
        scope.add('_waits', {'task_id': scope.task_id, 'stage_id': _stage.get(), 'kind': kind,
                             'duration_seconds': max(0.0, duration_seconds)})


def submit_observed(executor, function, *args, **kwargs):
    context = copy_context()
    queued = time.perf_counter()
    def run():
        record_wait('executor_queue', time.perf_counter() - queued)
        return function(*args, **kwargs)
    return executor.submit(context.run, run)


def map_observed(executor, function, items):
    futures = [submit_observed(executor, function, item) for item in items]
    try:
        for future in futures:
            yield future.result()
    finally:
        for future in futures:
            future.cancel()
