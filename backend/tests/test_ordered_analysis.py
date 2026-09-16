from contextlib import closing
import threading

import pytest

from app.services.ordered_analysis import ordered_analysis


@pytest.mark.parametrize("workers", [1, 2])
def test_analysis_preserves_order_and_acquires_inputs_on_caller(workers):
    caller = threading.get_ident()
    acquired = []
    second_started = threading.Event()

    def inputs():
        for value in range(7):
            assert threading.get_ident() == caller
            acquired.append(value)
            yield value

    def analyze(value):
        if workers > 1:
            assert threading.get_ident() != caller
            if value == 0:
                assert second_started.wait(5)
            elif value == 1:
                second_started.set()
        return value * 2

    with closing(ordered_analysis(analyze, inputs(), workers)) as results:
        assert next(results) == 0
        assert len(acquired) == workers
        assert list(results) == [2, 4, 6, 8, 10, 12]


def test_analysis_failure_propagates_without_reading_remaining_pages():
    acquired = []

    def inputs():
        for value in range(20):
            acquired.append(value)
            yield value

    def analyze(value):
        if value == 0:
            raise ValueError("invalid page analysis")
        return value

    with closing(ordered_analysis(analyze, inputs(), 2)) as results:
        with pytest.raises(ValueError, match="invalid page analysis"):
            next(results)
    assert acquired == [0, 1]


def test_closing_analysis_stops_acquisition_and_joins_workers():
    acquired = []
    worker_threads = []

    def inputs():
        for value in range(20):
            acquired.append(value)
            yield value

    def analyze(value):
        worker_threads.append(threading.current_thread())
        return value

    with closing(ordered_analysis(analyze, inputs(), 2)) as results:
        assert next(results) == 0
    assert acquired == [0, 1]
    assert all(not worker.is_alive() for worker in worker_threads)
