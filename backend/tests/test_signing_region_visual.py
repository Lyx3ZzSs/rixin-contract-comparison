from pathlib import Path

from app.models import BBox
from app.services.signing_region.models import SigningRegion
from app.services.signing_region.visual import LocalCpuVisualSignatureDetector, RemoteVisualSignatureDetector


def _region() -> SigningRegion:
    return SigningRegion(
        region_id="SR-1-1",
        page_no=1,
        bbox=BBox(x0=100, y0=600, x1=300, y1=780),
        confidence=0.8,
        confidence_reasons=["test"],
    )


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
