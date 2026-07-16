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


class _FakeCv2:
    COLOR_BGR2HSV = 1
    COLOR_BGR2GRAY = 2

    def __init__(self, np: Any) -> None:
        self.np = np

    def cvtColor(self, image: Any, code: int) -> Any:
        if code == self.COLOR_BGR2GRAY:
            return (image[:, :, 0] * 0.114 + image[:, :, 1] * 0.587 + image[:, :, 2] * 0.299).astype(
                self.np.uint8
            )
        if code != self.COLOR_BGR2HSV:
            raise ValueError(f"unsupported color conversion: {code}")

        bgr = image.astype(self.np.float32) / 255.0
        b = bgr[:, :, 0]
        g = bgr[:, :, 1]
        r = bgr[:, :, 2]
        max_channel = self.np.maximum(self.np.maximum(r, g), b)
        min_channel = self.np.minimum(self.np.minimum(r, g), b)
        delta = max_channel - min_channel

        hue = self.np.zeros_like(max_channel)
        nonzero_delta = delta != 0
        red_max = (max_channel == r) & nonzero_delta
        green_max = (max_channel == g) & nonzero_delta
        blue_max = (max_channel == b) & nonzero_delta
        hue[red_max] = (60 * ((g[red_max] - b[red_max]) / delta[red_max]) + 360) % 360
        hue[green_max] = 60 * ((b[green_max] - r[green_max]) / delta[green_max] + 2)
        hue[blue_max] = 60 * ((r[blue_max] - g[blue_max]) / delta[blue_max] + 4)
        hue = hue / 2

        saturation = self.np.zeros_like(max_channel)
        nonzero_value = max_channel != 0
        saturation[nonzero_value] = delta[nonzero_value] / max_channel[nonzero_value] * 255
        value = max_channel * 255
        return self.np.stack([hue, saturation, value], axis=2).astype(self.np.uint8)

    def inRange(self, image: Any, lower: Any, upper: Any) -> Any:
        return self.np.where(self.np.all((image >= lower) & (image <= upper), axis=2), 255, 0).astype(
            self.np.uint8
        )

    def bitwise_or(self, left: Any, right: Any) -> Any:
        return self.np.bitwise_or(left, right)


def _patch_opencv_dependencies(monkeypatch: pytest.MonkeyPatch, detector_cls: Any) -> None:
    import numpy as np

    monkeypatch.setattr(detector_cls, "_dependencies", staticmethod(lambda: (_FakeCv2(np), np)))


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


def test_opencv_visual_detector_reports_unavailable_when_pdf_missing() -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    detector = OpenCvVisualSignatureDetector()

    result = detector.detect(Path("missing.pdf"), [_region()], task_id="task-1")

    assert result.available is False
    assert result.error == "pdf_missing"
    assert result.detections == []


def test_opencv_visual_detector_reports_unavailable_when_dependencies_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(OpenCvVisualSignatureDetector, "_dependencies", staticmethod(lambda: None))
    detector = OpenCvVisualSignatureDetector()

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is False
    assert result.model_name == "opencv"
    assert result.error == "opencv_unavailable"
    assert result.detections == []


def test_opencv_visual_detector_reports_unavailable_when_all_regions_fail_to_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    _patch_opencv_dependencies(monkeypatch, OpenCvVisualSignatureDetector)
    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: None)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is False
    assert result.model_name == "opencv"
    assert result.error == "render_failed"
    assert result.detections == []


def test_opencv_visual_detector_detects_red_seal_in_region(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((240, 240, 3), 255, dtype=np.uint8)
    image[80:155, 90:170] = [0, 0, 255]

    _patch_opencv_dependencies(monkeypatch, OpenCvVisualSignatureDetector)
    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert result.model_name == "opencv"
    assert len(result.detections) == 1
    assert result.detections[0].label == "seal"
    assert result.detections[0].confidence >= 0.55
    assert result.detections[0].raw_data["red_pixel_ratio"] > 0


def test_opencv_visual_detector_detects_handwriting_density(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((240, 240, 3), 255, dtype=np.uint8)
    for offset in range(0, 80, 8):
        image[100 + offset : 104 + offset, 40 + offset : 150 + offset] = [20, 20, 20]

    _patch_opencv_dependencies(monkeypatch, OpenCvVisualSignatureDetector)
    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert len(result.detections) == 1
    assert result.detections[0].label == "signature"
    assert result.detections[0].confidence >= 0.55
    assert result.detections[0].raw_data["dark_pixel_ratio"] > 0


def test_opencv_visual_detector_does_not_treat_dense_printed_glyphs_as_signature(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((240, 240, 3), 255, dtype=np.uint8)
    for y in range(70, 170, 20):
        for x in range(30, 220, 16):
            image[y : y + 9, x : x + 7] = [20, 20, 20]
    image[190:191, 20:220] = [20, 20, 20]

    _patch_opencv_dependencies(monkeypatch, OpenCvVisualSignatureDetector)
    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert result.detections == []


def test_opencv_visual_detector_removes_printed_table_rules_before_handwriting_detection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "printed-table.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((240, 240, 3), 255, dtype=np.uint8)
    for y in (30, 80, 130, 190):
        image[y : y + 4, 20:220] = [20, 20, 20]
    for y in range(45, 180, 24):
        for x in range(35, 210, 22):
            image[y : y + 8, x : x + 6] = [20, 20, 20]

    _patch_opencv_dependencies(monkeypatch, OpenCvVisualSignatureDetector)
    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert result.detections == []


def test_opencv_visual_detector_keeps_blank_region_as_no_detection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((240, 240, 3), 255, dtype=np.uint8)

    _patch_opencv_dependencies(monkeypatch, OpenCvVisualSignatureDetector)
    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert result.detections == []


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
