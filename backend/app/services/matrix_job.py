"""A bounded, task-owned process for the independent matrix graphic scan."""
from __future__ import annotations

import multiprocessing
import logging
import threading
import time
from pathlib import Path

from app.errors import TaskCancelled

# A background scan has one analysis lane and one OpenCV lane. Two running
# comparison tasks can own at most two processes; excess callers stay inline.
_SLOTS = threading.BoundedSemaphore(2)
logger = logging.getLogger(__name__)


def _scan(send, cancelled, original: Path, compare: Path, alignment, cache_bytes: int):
    import cv2
    from app.services.matrix_graphics import MatrixGraphicComparator
    from app.services.page_render_cache import PageRenderCache

    started = time.perf_counter()
    cpu = time.process_time()
    cache = PageRenderCache(cache_bytes)
    try:
        cv2.setNumThreads(1)

        def check():
            if cancelled.is_set():
                raise TaskCancelled("任务已取消。")

        comparator = MatrixGraphicComparator(render_cache=cache, max_inflight=1, cancellation_check=check)
        result = comparator.compare(original, compare, alignment)
        send.send((True, result, {
            **comparator.performance.snapshot(),
            "started_at_perf_seconds": started,
            "finished_at_perf_seconds": time.perf_counter(),
            "cpu_seconds": time.process_time() - cpu,
            "render": cache.metrics.snapshot(),
            "execution": "background_process",
            "opencv_threads": 1,
        }))
    except BaseException as exc:
        send.send((False, exc, None))
    finally:
        cache.clear()
        send.close()


class MatrixScanJob:
    """Buffer output privately; only result() exposes it at Summary."""

    def __init__(self, process, receive, cancel_event):
        self.process = process
        self.receive = receive
        self.cancel_event = cancel_event
        self._closed = False
        self._ready = threading.Event()
        self._payload = None
        self._receive_error = None
        self._receiver = threading.Thread(target=self._receive_result, name="matrix-result", daemon=True)
        self.submitted_at_perf_seconds = time.perf_counter()

    def _receive_result(self):
        # Exactly one receiver per bounded job. Drain even before Summary so
        # send() cannot retain the child and its render cache behind a full pipe.
        try:
            self._payload = self.receive.recv()
        except BaseException as exc:
            self._receive_error = exc
        finally:
            self._ready.set()

    @classmethod
    def start(cls, original, compare, alignment, cache_bytes):
        if not _SLOTS.acquire(blocking=False):
            return None
        receive = send = process = job = None
        try:
            context = multiprocessing.get_context("spawn")
            receive, send = context.Pipe(duplex=False)
            cancel_event = context.Event()
            process = context.Process(target=_scan, args=(send, cancel_event, original, compare, alignment, cache_bytes),
                                      name="matrix-scan", daemon=True)
            job = cls(process, receive, cancel_event)
            process.start()
            send.close()
            send = None
            job._receiver.start()
            return job
        except BaseException as exc:
            logger.warning("Matrix scan startup failed", exc_info=True)
            if send is not None:
                _close_resource(send)
                send = None
            if job is not None:
                job.close()
            else:
                try:
                    if receive is not None:
                        _close_resource(receive)
                    if process is not None:
                        _close_resource(process)
                finally:
                    _SLOTS.release()
            if not isinstance(exc, Exception):
                raise
            return None
        finally:
            if send is not None:
                _close_resource(send)

    def result(self, cancellation_check=lambda: None):
        waiting = time.perf_counter()
        # Never recv on the pipeline thread: a readable pipe may contain only
        # the header of a large frame, not a complete unpickled result.
        while not self._ready.wait(0.05):
            cancellation_check()
        cancellation_check()
        if self._receive_error is not None:
            raise RuntimeError("Matrix scan worker closed without a complete result") from self._receive_error
        succeeded, result, metrics = self._payload
        if not succeeded:
            raise result
        metrics["submitted_at_perf_seconds"] = self.submitted_at_perf_seconds
        metrics["summary_wait_seconds"] = time.perf_counter() - waiting
        return result, metrics

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            try:
                self.cancel_event.set()
            except Exception:
                logger.warning("Could not signal matrix cancellation", exc_info=True)
            if self.process.pid is not None:
                for action, timeout in ((None, 1), (self.process.terminate, 2), (self.process.kill, 2)):
                    try:
                        if action is not None and self.process.is_alive():
                            action()
                        self.process.join(timeout=timeout)
                        if not self.process.is_alive():
                            break
                    except Exception:
                        logger.warning("Could not reap matrix worker", exc_info=True)
            # Child write end is now closed. A receiver blocked in a partial
            # frame gets EOF; joining it precedes releasing the job reservation.
            if self._receiver.ident is not None:
                self._receiver.join(timeout=2)
        except Exception:
            logger.warning("Matrix cleanup failed", exc_info=True)
        finally:
            _close_resource(self.receive)
            try:
                alive = self.process.pid is not None and self.process.is_alive()
                receiver_alive = self._receiver.is_alive()
            except Exception:
                logger.warning("Could not verify matrix cleanup", exc_info=True)
                alive = receiver_alive = True
            if alive or receiver_alive:
                # Do not permit unbounded replacement jobs if the OS failed to
                # reap a process/thread. Cleanup must not hide a pipeline error.
                logger.error("Matrix resources could not be reaped; retaining its bounded slot")
            else:
                _close_resource(self.process)
                self._payload = None
                _SLOTS.release()


def _close_resource(resource):
    try:
        resource.close()
    except Exception:
        logger.warning("Could not close matrix resource", exc_info=True)
