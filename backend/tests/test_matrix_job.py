import multiprocessing
import time

import fitz
import pytest

from app.errors import TaskCancelled
from app.services.matrix_graphics import MatrixGraphicComparator
from app.services.matrix_job import MatrixScanJob
from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus


def make_pdf(tmp_path):
    path = tmp_path / 'sample.pdf'
    with fitz.open() as document:
        page = document.new_page(width=200, height=200)
        page.insert_text((20, 30), 'contract body')
        document.save(path)
    return path


def alignment():
    return PageAlignmentResult((PageAlignmentEntry(PageAlignmentStatus.MATCHED, (1,), (1,), 1.0, (), 'test'),))


def test_background_matrix_matches_inline_and_owns_lifecycle(tmp_path):
    path = make_pdf(tmp_path)
    expected = MatrixGraphicComparator(max_inflight=2).compare(path, path, alignment())
    job = MatrixScanJob.start(path, path, alignment(), 1024**2)
    assert job is not None
    pid = job.process.pid
    try:
        actual, metrics = job.result()
        assert actual == expected
        assert metrics['counters']['pages_scanned'] == 2
        assert metrics['started_at_perf_seconds'] <= metrics['finished_at_perf_seconds']
        assert metrics['opencv_threads'] == 1
    finally:
        job.close()
    assert pid not in {p.pid for p in multiprocessing.active_children()}
    job.close()


def test_background_exception_is_raised_only_when_joining(tmp_path):
    path = make_pdf(tmp_path)
    job = MatrixScanJob.start(path, path, 'invalid alignment', 0)
    assert job is not None
    try:
        with pytest.raises(AttributeError, match='entry_for_original'):
            job.result()
    finally:
        job.close()


def test_cancel_reaps_worker(tmp_path):
    path = make_pdf(tmp_path)
    job = MatrixScanJob.start(path, path, alignment(), 0)
    assert job is not None
    pid = job.process.pid
    def cancel():
        raise TaskCancelled('cancel')
    try:
        with pytest.raises(TaskCancelled):
            job.result(cancel)
    finally:
        job.close()
    assert pid not in {p.pid for p in multiprocessing.active_children()}


def test_matrix_checks_cancellation_without_skipping_successful_scans(tmp_path):
    path = make_pdf(tmp_path)
    calls = []
    def cancel():
        calls.append(True)
        raise TaskCancelled('cancel')
    with pytest.raises(TaskCancelled):
        MatrixGraphicComparator(cancellation_check=cancel).compare(path, path, alignment())
    assert calls


def _send_large_result(send, cancelled, original, compare, alignment, cache_bytes):
    send.send((True, "x" * (2 * 1024 * 1024), {}))
    send.close()


def _send_partial_result(send, cancelled, original, compare, started, cache_bytes):
    import os
    import struct
    os.write(send.fileno(), struct.pack("!i", 2 * 1024 * 1024) + b"x")
    started.set()
    cancelled.wait(10)
    send.close()


def test_startup_constructor_failure_restores_slot_and_closes_pipes(monkeypatch, tmp_path):
    from app.services import matrix_job
    real_context = multiprocessing.get_context("spawn")
    pipes = []
    class BrokenContext:
        def Pipe(self, **kwargs):
            endpoints = real_context.Pipe(**kwargs)
            pipes.extend(endpoints)
            return endpoints
        def Event(self):
            raise OSError("semaphore exhausted")
    with monkeypatch.context() as patch:
        patch.setattr(matrix_job.multiprocessing, "get_context", lambda _: BrokenContext())
        assert MatrixScanJob.start(tmp_path / "a", tmp_path / "b", None, 0) is None
        assert MatrixScanJob.start(tmp_path / "a", tmp_path / "b", None, 0) is None
    assert all(endpoint.closed for endpoint in pipes)
    assert matrix_job._SLOTS.acquire(blocking=False)
    assert matrix_job._SLOTS.acquire(blocking=False)
    matrix_job._SLOTS.release()
    matrix_job._SLOTS.release()


def test_slots_are_bounded_and_released_after_close(monkeypatch, tmp_path):
    from app.services import matrix_job
    monkeypatch.setattr(matrix_job, "_scan", _send_large_result)
    jobs = []
    try:
        jobs = [MatrixScanJob.start(tmp_path, tmp_path, None, 0) for _ in range(2)]
        assert all(job is not None for job in jobs)
        assert MatrixScanJob.start(tmp_path, tmp_path, None, 0) is None
        jobs[0].close()
        replacement = MatrixScanJob.start(tmp_path, tmp_path, None, 0)
        assert replacement is not None
        jobs.append(replacement)
    finally:
        for job in jobs:
            if job is not None:
                job.close()


def test_large_result_drains_before_summary_wait(monkeypatch, tmp_path):
    from app.services import matrix_job
    monkeypatch.setattr(matrix_job, "_scan", _send_large_result)
    job = MatrixScanJob.start(tmp_path, tmp_path, None, 0)
    assert job is not None
    try:
        job.process.join(timeout=3)
        assert not job.process.is_alive(), "receiver must drain beyond the pipe buffer before Summary"
        value, _ = job.result()
        assert len(value) == 2 * 1024 * 1024
    finally:
        job.close()


def test_cancellation_interrupts_partial_large_receive(monkeypatch, tmp_path):
    import threading
    from app.services import matrix_job
    monkeypatch.setattr(matrix_job, "_scan", _send_partial_result)
    started = multiprocessing.get_context("spawn").Event()
    job = MatrixScanJob.start(tmp_path, tmp_path, started, 0)
    assert job is not None
    cancel_requested = threading.Event()
    outcomes = []
    def check():
        if cancel_requested.is_set():
            raise TaskCancelled("cancel partial receive")
    def wait_result():
        try:
            job.result(check)
        except BaseException as exc:
            outcomes.append(exc)
    waiter = threading.Thread(target=wait_result, daemon=True)
    try:
        assert started.wait(3)
        waiter.start()
        time.sleep(0.1)
        cancel_requested.set()
        waiter.join(timeout=0.5)
        assert not waiter.is_alive(), "result must remain cancellable while recv has only a partial frame"
        assert len(outcomes) == 1 and isinstance(outcomes[0], TaskCancelled)
    finally:
        job.close()
        waiter.join(timeout=2)
    assert not job._receiver.is_alive()


def _send_exception_result(send, cancelled, original, compare, error, cache_bytes):
    send.send((False, error, None))
    send.close()


@pytest.mark.parametrize("error", [SystemExit(7), TaskCancelled("cancelled"), RuntimeError("broken")])
def test_worker_base_exceptions_remain_deferred_to_result(monkeypatch, tmp_path, error):
    from app.services import matrix_job
    monkeypatch.setattr(matrix_job, "_scan", _send_exception_result)
    job = MatrixScanJob.start(tmp_path, tmp_path, error, 0)
    assert job is not None
    try:
        assert job._ready.wait(3)
        with pytest.raises(type(error)) as captured:
            job.result()
        assert captured.value.args == error.args
    finally:
        job.close()


def test_startup_base_exception_releases_slot_without_becoming_fallback(monkeypatch, tmp_path):
    from app.services import matrix_job
    def interrupt(_):
        raise KeyboardInterrupt("startup interrupted")
    with monkeypatch.context() as patch:
        patch.setattr(matrix_job.multiprocessing, "get_context", interrupt)
        with pytest.raises(KeyboardInterrupt):
            MatrixScanJob.start(tmp_path, tmp_path, None, 0)
    acquired = 0
    try:
        for _ in range(2):
            if matrix_job._SLOTS.acquire(blocking=False):
                acquired += 1
        assert acquired == 2
    finally:
        for _ in range(acquired):
            matrix_job._SLOTS.release()
