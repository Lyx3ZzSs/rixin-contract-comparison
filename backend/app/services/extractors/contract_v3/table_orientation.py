"""Read-only OCR direction probes. Probe text never becomes canonical text."""

from __future__ import annotations

import base64
from collections import OrderedDict
from copy import deepcopy
import hashlib
from io import BytesIO
import math
import threading
import time

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from ..base import DocumentExtractionError
from .artifact import enclosing, rectangle, transform_point


class Recognition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    text: str = Field(max_length=4096)
    score: float = Field(ge=0, le=1)


class ScoreResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: int
    request_id: str
    preserves_input_orientation: bool
    model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    angles: dict[str, list[Recognition]]


def decide_direction(angles, *, min_score=0.85, margin=0.08):
    """Compare identical sampled regions; sparse/numeric evidence cannot authorize rotation."""
    summary = {}
    for angle in (0, 90, 270):
        items = angles[str(angle)]
        valid = [r for r in items if r["text"].strip()]
        directional = sum(any(c.isalpha() for c in r["text"]) for r in valid)
        summary[str(angle)] = dict(
            score=sum(r["score"] for r in valid) / len(valid) if valid else 0,
            valid_count=len(valid),
            directional_count=directional,
        )
    base = dict(angle_ccw=0, scores=summary)
    zero = summary["0"]
    if zero["valid_count"] >= 5 and zero["directional_count"] >= 3 and zero["score"] >= 0.90:
        return base | dict(status="KEEP", reason="RELIABLE_ZERO")
    ranked = sorted((0, 90, 270), key=lambda a: summary[str(a)]["score"], reverse=True)
    best, runner_up = ranked[:2]
    winner = summary[str(best)]
    if winner["valid_count"] < 5 or winner["directional_count"] < 3:
        return base | dict(status="UNCERTAIN", reason="INSUFFICIENT_DIRECTIONAL_TEXT")
    if best == 0:
        return base | dict(status="KEEP", reason="ZERO_BEST")
    if (
        winner["score"] < min_score
        or winner["score"] - zero["score"] < margin
        or winner["score"] - summary[str(runner_up)]["score"] < margin
    ):
        return base | dict(status="UNCERTAIN", reason="AMBIGUOUS_SCORES")
    # A few surviving words must not beat a direction with materially better coverage.
    if winner["valid_count"] < max(s["valid_count"] for s in summary.values()) * 0.8:
        return base | dict(status="UNCERTAIN", reason="COVERAGE_LOSS")
    return base | dict(status="ROTATE", angle_ccw=best, reason="OCR_DIRECTION_CONFIRMED")


class TableOrientation:
    _slots = threading.BoundedSemaphore(1)

    def __init__(self, config: Settings, client: httpx.Client | None = None):
        self.config, self.client = config, client
        self.reset()

    def reset(self):
        self.elapsed = 0.0
        self.cache = OrderedDict()

    def analyze(self, crop, transform, artifact, region, tokens):
        if self.config.table_orientation_mode == "off":
            return dict(status="KEEP", angle_ccw=0, reason="DISABLED")
        if tokens and all(t.get("source") == "native_pdf" for t in tokens):
            return dict(status="KEEP", angle_ccw=0, reason="NATIVE_TEXT")
        known_text = "".join(b.get("text", "") for b in artifact["blocks"] if b.get("region_id") == region["region_id"])
        if known_text.strip() and not any(c.isalpha() for c in known_text):
            return dict(status="UNCERTAIN", angle_ccw=0, reason="NON_DIRECTIONAL_TEXT")
        started = time.monotonic()
        remaining = self.config.table_orientation_document_budget - self.elapsed
        if remaining <= 0:
            return dict(status="FAILED", angle_ccw=0, reason="DOCUMENT_BUDGET_EXHAUSTED")
        deadline = started + min(remaining, self.config.table_orientation_timeout)
        diagnostics = dict(status="FAILED", angle_ccw=0, detection_requests=0, score_requests=0)
        try:
            boxes = []
            h, w = crop.shape[:2]
            for block in artifact["blocks"]:
                if block.get("region_id") != region["region_id"] or block.get("source") != "PP-OCRv6":
                    continue
                polygon = block.get("polygon") or (rectangle(block["bbox"]) if block.get("bbox") else None)
                if polygon:
                    boxes.append(enclosing([transform_point(p, transform["page_to_model"]) for p in polygon]))
            if not boxes:
                # Only detection geometry is reused from full OCR; its recognition may auto-rotate.
                from .ocr import SelectiveOCRRouter
                from ..ppocrv5 import PPOCRV5Extractor

                image = Image.fromarray(crop[:, :, ::-1])
                encoded = BytesIO()
                image.save(encoded, format="PNG")
                cfg = self.config.model_copy(update={"ppocrv5_timeout_seconds": max(0.01, deadline - time.monotonic())})
                diagnostics["detection_requests"] = 1
                payload = SelectiveOCRRouter(cfg)._request(encoded.getvalue())
                result = PPOCRV5Extractor(cfg)._normalize_remote_payload(payload)
                if len(result) != 1:
                    raise ValueError("INVALID_DETECTION_PAGE_COUNT")
                boxes = [enclosing(p) for p in result[0].get("dt_polys", result[0].get("rec_polys", []))]
            boxes = sorted(
                {
                    tuple(b)
                    for b in boxes
                    if len(b) == 4
                    and all(math.isfinite(x) for x in b)
                    and 0 <= b[0] < b[2] <= w
                    and 0 <= b[1] < b[3] <= h
                },
                key=lambda b: (b[1], b[0]),
            )
            vertical = [b for b in boxes if (b[2] - b[0]) / (b[3] - b[1]) < 0.8]
            diagnostics.update(box_count=len(boxes), vertical_count=len(vertical))
            if len(vertical) < 3 or len(vertical) < len(boxes) * 0.28:
                return diagnostics | dict(status="KEEP", reason="NO_ROTATION_EVIDENCE")
            if len(boxes) > 18:
                boxes = [boxes[round(i * (len(boxes) - 1) / 17)] for i in range(18)]
            image = Image.fromarray(crop[:, :, ::-1])
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            png = buffer.getvalue()
            key = hashlib.sha256(png + repr(boxes).encode()).hexdigest()
            if key in self.cache:
                return deepcopy(self.cache[key]) | dict(
                    cache_hit=True, detection_requests=diagnostics["detection_requests"], score_requests=0
                )
            endpoint = self.config.table_orientation_endpoint.rstrip("/")
            if not endpoint:
                raise ValueError("ORIENTATION_ENDPOINT_UNAVAILABLE")
            if not endpoint.endswith("/orientation-score"):
                endpoint += "/orientation-score"
            body = dict(schema_version=1, request_id=key, image=base64.b64encode(png).decode(), boxes=boxes)
            wait = deadline - time.monotonic()
            if wait <= 0 or not self._slots.acquire(timeout=wait):
                raise TimeoutError("ORIENTATION_QUEUE_TIMEOUT")
            try:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    raise TimeoutError("ORIENTATION_BUDGET_EXHAUSTED")
                diagnostics["score_requests"] = 1
                if self.client is None:
                    with httpx.Client(timeout=timeout) as client:
                        response = client.post(endpoint, json=body)
                else:
                    response = self.client.post(endpoint, json=body, timeout=timeout)
                response.raise_for_status()
                scores = ScoreResponse.model_validate(response.json())
            finally:
                self._slots.release()
            if (
                time.monotonic() > deadline
                or scores.schema_version != 1
                or scores.request_id != key
                or not scores.preserves_input_orientation
                or set(scores.angles) != {"0", "90", "270"}
                or any(len(v) != len(boxes) for v in scores.angles.values())
            ):
                raise ValueError("INVALID_ORIENTATION_RESPONSE_OR_DEADLINE")
            decision = decide_direction(
                {k: [v.model_dump() for v in values] for k, values in scores.angles.items()},
                min_score=self.config.table_orientation_min_score,
                margin=self.config.table_orientation_margin,
            )
            diagnostics.update(decision, model_sha256=scores.model_sha256, sample_count=len(boxes))
            self.cache[key] = deepcopy(diagnostics)
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
            return diagnostics
        except (httpx.HTTPError, DocumentExtractionError, ValueError, TimeoutError, KeyError, TypeError) as exc:
            return diagnostics | dict(reason="ORIENTATION_PROBE_FAILED", error=type(exc).__name__)
        finally:
            self.elapsed += time.monotonic() - started
