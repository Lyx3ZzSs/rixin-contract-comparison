import pytest

from app.services.pipeline_metrics import PerformanceRecorder
from app.services.remote_metrics import record_remote_metrics


def test_remote_evidence_is_explicit_and_separate_from_client_timings():
    recorder = PerformanceRecorder()
    record_remote_metrics(recorder, {"Server-Timing": 'queue;dur=12.5, ocr;dur=300;desc="inference"'}, {"logId": "run-123"})
    result = recorder.snapshot()
    assert result["operations"]["remote_reported_queue"]["duration_seconds"] == 0.0125
    assert result["operations"]["remote_reported_ocr"]["duration_seconds"] == 0.3
    assert result["attributes"] == {"remote_request_id": "run-123"}
    assert result["counters"]["remote_timing_available"] == 1
    assert "http_request" not in result["operations"]


@pytest.mark.parametrize("value", ["", "ocr;dur=-4", "ocr;dur=nan", "ocr;dur=Infinity", 'ocr;dur=oops', 'ocr;dur=1x'])
def test_unavailable_or_invalid_timing_does_not_invent_inference_time(value):
    recorder = PerformanceRecorder()
    record_remote_metrics(recorder, {"Server-Timing": value, "Authorization": "secret"}, {"logId": "bad\nvalue", "text": "private"})
    result = recorder.snapshot()
    assert result["counters"]["remote_timing_available"] == 0
    assert result["operations"] == {}
    assert "attributes" not in result
