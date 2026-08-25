from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any


STRUCTURED_EVENT_FIELDS = (
    "timestamp",
    "level",
    "event",
    "request_id",
    "task_id",
    "job_id",
    "execution_no",
    "attempt",
    "worker_id",
    "from_status",
    "to_status",
    "terminal_reason",
    "task_revision",
    "report_revision",
    "error_type",
    "error_code",
    "duration_ms",
)
_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]+$")
_ERROR_CODE = re.compile(r"^[A-Z0-9_]+$")


class JsonFormatter(logging.Formatter):
    """Render structured execution events without including payload text."""

    def format(self, record: logging.LogRecord) -> str:
        payload = dict(getattr(record, "structured_event", _empty_event("unstructured_log", record.levelname)))
        payload["timestamp"] = datetime.now(UTC).isoformat()
        payload["level"] = record.levelname
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def log_event(logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a stable, metadata-only execution event.

    The helper deliberately accepts only the documented field set.  It never
    serializes exception messages, document/OCR contents, authentication
    tokens, or filesystem paths.
    """
    payload = _empty_event(event, logging.getLevelName(level))
    for name in STRUCTURED_EVENT_FIELDS:
        if name in {"timestamp", "level", "event"} or name not in fields:
            continue
        payload[name] = _safe_field(name, fields[name])
    logger.log(level, payload["event"], extra={"structured_event": payload})


def _empty_event(event: str, level: str) -> dict[str, Any]:
    if not _EVENT_NAME.fullmatch(event):
        raise ValueError("Structured event names must be stable lowercase identifiers")
    payload = {name: None for name in STRUCTURED_EVENT_FIELDS}
    payload["timestamp"] = datetime.now(UTC).isoformat()
    payload["level"] = str(level)
    payload["event"] = event
    return payload


def _safe_field(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in {"execution_no", "attempt", "task_revision", "report_revision", "duration_ms"}:
        return value if isinstance(value, int | float) and not isinstance(value, bool) else None
    text = str(value).strip()
    if name == "error_type":
        return _safe_error_type(text)
    if not text or "/" in text or "\\" in text or _contains_secret(text):
        return None
    if name == "error_code":
        return text if _ERROR_CODE.fullmatch(text) else None
    return text if _IDENTIFIER.fullmatch(text) else None


def _contains_secret(value: str) -> bool:
    lowered = value.lower()
    return any(fragment in lowered for fragment in ("token", "authorization", "bearer", "password", "secret"))


def _safe_error_type(value: str) -> str | None:
    candidate = value.split(":", 1)[0]
    normalized = "".join(part if part.isupper() else part.capitalize() for part in re.findall(r"[A-Za-z]+", candidate))
    return normalized or None


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
    )
