from pathlib import Path
from typing import Any

import pytest

from app.models import BBox
from app.services.signing_region.models import SigningRegion
from app.services.signing_region.visual import (
    LocalCpuVisualSignatureDetector,
    OpenCvSigningRegionFingerprinter,
    RemoteVisualSignatureDetector,
)


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.payload


def _region() -> SigningRegion:
    return SigningRegion(
        region_id="SR-1-1",
        page_no=1,
        bbox=BBox(x0=100, y0=600, x1=300, y1=780),
        confidence=0.8,
        confidence_reasons=["test"],
    )


def test_opencv_fingerprinter_returns_unavailable_for_missing_pdf() -> None:
    region = SigningRegion(
        region_id="SR-1",
        page_no=1,
        bbox=BBox(x0=60, y0=620, x1=520, y1=740),
        confidence=0.9,
    )

    result = OpenCvSigningRegionFingerprinter().fingerprint_region(Path("missing.pdf"), region)

    assert result["status"] == "unavailable"
    assert result["reason"] == "pdf_missing"


def test_local_visual_detector_without_model_is_unavailable() -> None:
    detector = LocalCpuVisualSignatureDetector(model_path="")

    result = detector.detect(Path("sample.pdf"), [_region()], task_id="task-1")

    assert result.available is False
    assert result.error == "local_model_not_configured"
    assert result.detections == []


def test_remote_visual_detector_without_url_is_unavailable() -> None:
    detector = RemoteVisualSignatureDetector(base_url="")

    result = detector.detect(Path("sample.pdf"), [_region()], task_id="task-1")

    assert result.available is False
    assert result.error == "remote_url_not_configured"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"detections": ["not-a-dict"]},
        {"detections": [{"page_no": "bad", "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}]},
        {"detections": [{"page_no": 1, "bbox": {"x0": "bad"}}]},
        {
            "detections": [
                {
                    "page_no": 1,
                    "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
                    "confidence": "bad",
                }
            ]
        },
    ],
)
def test_remote_visual_detector_malformed_payload_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
) -> None:
    def fake_post(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr("app.services.signing_region.visual.httpx.post", fake_post)
    detector = RemoteVisualSignatureDetector(base_url="http://visual-detector")

    result = detector.detect(Path("sample.pdf"), [_region()], task_id="task-1")

    assert result.available is False
    assert result.error == "remote_call_failed"
    assert result.detections == []
