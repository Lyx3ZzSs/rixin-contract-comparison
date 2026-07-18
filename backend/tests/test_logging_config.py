from __future__ import annotations

import io
import json
import logging
from pathlib import Path

def test_structured_event_contains_all_stable_fields_with_nulls() -> None:
    from app import logging_config

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging_config.JsonFormatter())
    logger = logging.getLogger("test.structured-event")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logging_config.log_event(logger, "job_claimed", task_id="TASK_1", job_id="JOB_1")

    payload = json.loads(stream.getvalue())
    assert set(payload) == set(logging_config.STRUCTURED_EVENT_FIELDS)
    assert payload["event"] == "job_claimed"
    assert payload["task_id"] == "TASK_1"
    assert payload["job_id"] == "JOB_1"
    assert payload["error_code"] is None


def test_structured_event_never_serializes_contract_text_tokens_or_absolute_paths() -> None:
    from app import logging_config

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging_config.JsonFormatter())
    logger = logging.getLogger("test.structured-event-sanitized")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logging_config.log_event(
        logger,
        "compensation_failed",
        task_id="TASK_2",
        error_type="OCR text: confidential contract /tmp/secret.pdf token=super-secret",
        recovery_marker=str(Path("/tmp/secret-marker.json")),
    )

    raw = stream.getvalue()
    payload = json.loads(raw)
    assert "confidential contract" not in raw
    assert "super-secret" not in raw
    assert "/tmp/" not in raw
    assert payload["error_type"] == "OCRText"
    assert payload["recovery_marker"] is None
