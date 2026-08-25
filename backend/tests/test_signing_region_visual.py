from pathlib import Path
from typing import Any

import pytest

from app.models import BBox
from app.services.signing_region.models import SigningElement, SigningElementType, SigningRegion, SigningRegionRole
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
            return (image[:, :, 0] * 0.114 + image[:, :, 1] * 0.587 + image[:, :, 2] * 0.299).astype(self.np.uint8)
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
        return self.np.where(self.np.all((image >= lower) & (image <= upper), axis=2), 255, 0).astype(self.np.uint8)

    def bitwise_or(self, left: Any, right: Any) -> Any:
        return self.np.bitwise_or(left, right)

    def connectedComponentsWithStats(self, image: Any, connectivity: int = 8) -> tuple[int, Any, Any, Any]:
        height, width = image.shape
        labels = self.np.zeros((height, width), dtype=self.np.int32)
        stats = [[0, 0, width, height, int(self.np.count_nonzero(image == 0))]]
        centroids = [[width / 2, height / 2]]
        offsets = [
            (-1, -1),
            (-1, 0),
            (-1, 1),
            (0, -1),
            (0, 1),
            (1, -1),
            (1, 0),
            (1, 1),
        ]
        if connectivity == 4:
            offsets = [(-1, 0), (0, -1), (0, 1), (1, 0)]

        label = 0
        for start_y, start_x in zip(*self.np.nonzero(image)):
            if labels[start_y, start_x] != 0:
                continue
            label += 1
            stack = [(int(start_y), int(start_x))]
            labels[start_y, start_x] = label
            points: list[tuple[int, int]] = []
            while stack:
                y, x = stack.pop()
                points.append((y, x))
                for dy, dx in offsets:
                    next_y, next_x = y + dy, x + dx
                    if not (0 <= next_y < height and 0 <= next_x < width):
                        continue
                    if image[next_y, next_x] == 0 or labels[next_y, next_x] != 0:
                        continue
                    labels[next_y, next_x] = label
                    stack.append((next_y, next_x))
            ys = [point[0] for point in points]
            xs = [point[1] for point in points]
            stats.append([min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, len(points)])
            centroids.append([sum(xs) / len(xs), sum(ys) / len(ys)])

        return label + 1, labels, self.np.asarray(stats), self.np.asarray(centroids)


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


def test_opencv_visual_detector_detects_red_seal_in_region(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_opencv_visual_detector_returns_local_bbox_for_dominant_adjacent_seal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((240, 240, 3), 255, dtype=np.uint8)
    image[30:80, 70:170] = [0, 0, 255]
    image[5:25, 230:238] = [0, 0, 255]
    rendered_regions: list[SigningRegion] = []

    _patch_opencv_dependencies(monkeypatch, OpenCvVisualSignatureDetector)
    detector = OpenCvVisualSignatureDetector()

    def render_region(_pdf_path: Path, region: SigningRegion) -> Any:
        rendered_regions.append(region)
        return image

    monkeypatch.setattr(detector, "_render_region", render_region)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert any(region.bbox.y0 < _region().bbox.y0 for region in rendered_regions)
    assert len(result.detections) == 1
    assert result.detections[0].label == "seal"
    assert result.detections[0].bbox.y0 < _region().bbox.y0
    assert result.detections[0].bbox.x0 == pytest.approx(158.33, abs=0.1)
    assert result.detections[0].bbox.y0 == pytest.approx(580.5, abs=0.1)
    assert result.detections[0].bbox.x1 == pytest.approx(241.67, abs=0.1)
    assert result.detections[0].bbox.y1 == pytest.approx(628.0, abs=0.1)
    assert result.detections[0].raw_data["red_component_count"] == 2.0


def test_opencv_visual_detector_ignores_tiny_red_speck(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((80, 80, 3), 255, dtype=np.uint8)
    image[20:24, 20:24] = [0, 0, 255]

    _patch_opencv_dependencies(monkeypatch, OpenCvVisualSignatureDetector)
    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert result.detections == []


def test_opencv_visual_detector_ignores_thin_red_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((240, 240, 3), 255, dtype=np.uint8)
    image[180:186, 70:150] = [0, 0, 255]

    _patch_opencv_dependencies(monkeypatch, OpenCvVisualSignatureDetector)
    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert result.detections == []


def test_opencv_visual_detector_ignores_scattered_red_noise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((80, 80, 3), 255, dtype=np.uint8)
    for index in range(12):
        y = 4 + (index // 4) * 20
        x = 4 + (index % 4) * 18
        image[y : y + 2, x : x + 2] = [0, 0, 255]

    _patch_opencv_dependencies(monkeypatch, OpenCvVisualSignatureDetector)
    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert result.detections == []


def test_opencv_visual_detector_detects_handwriting_density(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_opencv_visual_detector_probes_below_empty_signature_field() -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    region = SigningRegion(
        region_id="SR-13-1",
        page_no=13,
        bbox=BBox(x0=60, y0=72, x1=544, y1=551),
        region_role=SigningRegionRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_reasons=["two_column_layout"],
        elements=[
            SigningElement(
                element_id="signature-label",
                element_type=SigningElementType.FIELD,
                page_no=13,
                bbox=BBox(x0=78, y0=188, x1=230, y1=209),
                text="",
                confidence=0.9,
                source="inferred",
                raw_ref={
                    "party_role": "甲方",
                    "field_key": "legal_representative",
                    "field_label": "法定代表人或授权代表签字",
                },
            ),
            SigningElement(
                element_id="date",
                element_type=SigningElementType.FIELD,
                page_no=13,
                bbox=BBox(x0=78, y0=280, x1=230, y1=302),
                text="2026年5月6日",
                confidence=0.9,
                source="inferred",
                raw_ref={"party_role": "甲方", "field_key": "date", "field_label": "签署日期"},
            ),
        ],
    )

    probes = OpenCvVisualSignatureDetector._signature_probe_regions(region)

    assert len(probes) == 1
    assert probes[0].bbox.y0 == 209
    assert probes[0].bbox.y1 == 280
    assert probes[0].bbox.x0 <= 78
    assert probes[0].bbox.x1 > 230


def test_opencv_visual_detector_stops_signature_probe_before_following_date() -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    region = SigningRegion(
        region_id="SR-53-3",
        page_no=53,
        bbox=BBox(x0=71.5, y0=65.5, x1=513.5, y1=541.5),
        region_role=SigningRegionRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_reasons=["two_column_layout"],
        elements=[
            SigningElement(
                element_id="email",
                element_type=SigningElementType.FIELD,
                page_no=53,
                bbox=BBox(x0=92, y0=407.5, x1=234, y1=423),
                text="12048144@ceic.com",
                confidence=0.9,
                source="inferred",
                raw_ref={"party_role": "甲方", "field_key": "email", "field_label": "邮箱"},
            ),
            SigningElement(
                element_id="signature-label",
                element_type=SigningElementType.FIELD,
                page_no=53,
                bbox=BBox(x0=91.5, y0=439, x1=148, y1=459.5),
                text="",
                confidence=0.9,
                source="inferred",
                raw_ref={"party_role": "甲方", "field_key": "signature", "field_label": "代表签字"},
            ),
            SigningElement(
                element_id="date",
                element_type=SigningElementType.FIELD,
                page_no=53,
                bbox=BBox(x0=88.5, y0=502, x1=195, y1=527.5),
                text="2026年5月6日",
                confidence=0.9,
                source="inferred",
                raw_ref={"party_role": "甲方", "field_key": "date", "field_label": "签署日期"},
            ),
        ],
    )

    probe = OpenCvVisualSignatureDetector._signature_probe_regions(region)[0]

    assert probe.bbox.x0 == 148
    assert probe.bbox.x1 == 292.5
    assert probe.bbox.y0 >= 423
    assert probe.bbox.y1 <= 502


def test_opencv_visual_detector_uses_full_width_for_stacked_signature_rows() -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    region = SigningRegion(
        region_id="SR-29-2",
        page_no=29,
        bbox=BBox(x0=93.5, y0=89.5, x1=410, y1=428.5),
        region_role=SigningRegionRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_reasons=["stacked_party_layout"],
        elements=[
            SigningElement(
                element_id="legal-signature",
                element_type=SigningElementType.FIELD,
                page_no=29,
                bbox=BBox(x0=117.5, y0=174, x1=365, y1=198),
                text="",
                confidence=0.9,
                source="inferred",
                raw_ref={
                    "party_role": "甲方",
                    "field_key": "legal_representative",
                    "field_label": "法定代表人（负责人）/授权代表（签字）",
                },
            ),
            SigningElement(
                element_id="date",
                element_type=SigningElementType.FIELD,
                page_no=29,
                bbox=BBox(x0=110.5, y0=211, x1=335.5, y1=237.5),
                text="2026年5月8日",
                confidence=0.9,
                source="inferred",
                raw_ref={"party_role": "甲方", "field_key": "date", "field_label": "时间"},
            ),
        ],
    )

    probe = OpenCvVisualSignatureDetector._signature_probe_regions(region)[0]

    assert probe.bbox.x0 == 365
    assert probe.bbox.x1 == 410
    assert probe.bbox.y0 < 174
    assert probe.bbox.y1 > 211


def test_opencv_visual_detector_probes_above_truncated_stacked_legal_label() -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    region = SigningRegion(
        region_id="SR-29-2",
        page_no=29,
        bbox=BBox(x0=93.5, y0=89.5, x1=410, y1=428.5),
        region_role=SigningRegionRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_reasons=["stacked_party_layout"],
        elements=[
            SigningElement(
                element_id="legal-signature",
                element_type=SigningElementType.FIELD,
                page_no=29,
                bbox=BBox(x0=113, y0=348, x1=247.5, y1=369.5),
                text="",
                confidence=0.9,
                source="inferred",
                raw_ref={
                    "party_role": "乙方",
                    "field_key": "legal_representative",
                    "field_label": "法定代表人（负责人）",
                },
            ),
            SigningElement(
                element_id="date",
                element_type=SigningElementType.FIELD,
                page_no=29,
                bbox=BBox(x0=107.5, y0=386, x1=336.5, y1=413),
                text="2026年5月8日",
                confidence=0.9,
                source="inferred",
                raw_ref={"party_role": "乙方", "field_key": "date", "field_label": "时间"},
            ),
        ],
    )

    probe = OpenCvVisualSignatureDetector._signature_probe_regions(region)[0]

    assert probe.bbox.x0 == pytest.approx(189.665)
    assert probe.bbox.x1 == 279.75
    assert probe.bbox.y0 == pytest.approx(299.625)
    assert probe.bbox.y1 == 348


def test_opencv_handwriting_bbox_ignores_probe_edge_printed_fragment() -> None:
    import cv2
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    mask = np.zeros((80, 145), dtype=np.uint8)
    mask[20:50, 0:20] = 1
    mask[18:62, 46:88] = 1

    bbox = OpenCvVisualSignatureDetector._meaningful_component_bbox(mask, cv2, min_area=4)

    assert bbox is not None
    assert bbox[0] == 46
    assert bbox[2] == 42


def test_opencv_visual_detector_prefers_explicit_signature_fields() -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    elements = []
    for role, x0 in (("甲方", 78), ("乙方", 320)):
        elements.extend(
            [
                SigningElement(
                    element_id=f"{role}-legal-representative",
                    element_type=SigningElementType.FIELD,
                    page_no=9,
                    bbox=BBox(x0=x0, y0=236, x1=x0 + 152, y1=257),
                    text="",
                    confidence=0.9,
                    source="inferred",
                    raw_ref={
                        "party_role": role,
                        "field_key": "legal_representative",
                        "field_label": "法人代表或授权代表",
                    },
                ),
                SigningElement(
                    element_id=f"{role}-signature",
                    element_type=SigningElementType.FIELD,
                    page_no=9,
                    bbox=BBox(x0=x0, y0=269, x1=x0 + 152, y1=292),
                    text="",
                    confidence=0.9,
                    source="inferred",
                    raw_ref={
                        "party_role": role,
                        "field_key": "signature",
                        "field_label": "(签字)",
                    },
                ),
            ]
        )
    region = SigningRegion(
        region_id="SR-9-1",
        page_no=9,
        bbox=BBox(x0=60, y0=72, x1=544, y1=551),
        region_role=SigningRegionRole.BOTH_PARTIES,
        confidence=0.9,
        elements=elements,
    )

    probes = OpenCvVisualSignatureDetector._signature_probe_regions(region)

    assert len(probes) == 2
    assert {(probe.bbox.x0, probe.bbox.x1) for probe in probes} == {
        (230, 302),
        (472, 544),
    }
    assert all(probe.bbox.y0 < 269 and probe.bbox.y1 > 292 for probe in probes)


def test_opencv_visual_detector_excludes_narrow_printed_table_signature_label() -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    label_bbox = BBox(x0=390, y0=503, x1=419, y1=520)
    region = SigningRegion(
        region_id="SR-2-1",
        page_no=2,
        bbox=BBox(x0=37, y0=242, x1=556, y1=687),
        region_role=SigningRegionRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_reasons=["two_column_layout"],
        elements=[
            SigningElement(
                element_id="signature-label",
                element_type=SigningElementType.FIELD,
                page_no=2,
                bbox=label_bbox,
                text="",
                confidence=0.99,
                source="inferred",
                raw_ref={
                    "party_role": "乙方",
                    "field_key": "signature",
                    "field_label": "(签字)",
                    "source_block_ids": ["p2-table-1-4-1"],
                },
            )
        ],
    )

    probe = OpenCvVisualSignatureDetector._signature_probe_regions(region)[0]

    assert probe.bbox.x0 == label_bbox.x1
    assert probe.bbox.x1 == region.bbox.x1
    assert probe.bbox.y0 < label_bbox.y0
    assert probe.bbox.y1 > label_bbox.y1
    assert probe.bbox != label_bbox


def test_opencv_visual_detector_probes_authorized_signature_inline() -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    region = SigningRegion(
        region_id="SR-12-1",
        page_no=12,
        bbox=BBox(x0=60, y0=79, x1=544, y1=555),
        region_role=SigningRegionRole.BOTH_PARTIES,
        confidence=0.9,
        elements=[
            SigningElement(
                element_id="legal-representative",
                element_type=SigningElementType.FIELD,
                page_no=12,
                bbox=BBox(x0=90, y0=210, x1=224, y1=226),
                text="",
                confidence=0.9,
                source="inferred",
                raw_ref={
                    "party_role": "甲方",
                    "field_key": "legal_representative",
                    "field_label": "法定代表人（负责人）或",
                },
            ),
            SigningElement(
                element_id="authorized-signature",
                element_type=SigningElementType.FIELD,
                page_no=12,
                bbox=BBox(x0=91, y0=232, x1=206, y1=249),
                text="",
                confidence=0.9,
                source="inferred",
                raw_ref={
                    "party_role": "甲方",
                    "field_key": "authorized_representative",
                    "field_label": "授权代表（签字）",
                },
            ),
        ],
    )

    probes = OpenCvVisualSignatureDetector._signature_probe_regions(region)

    assert len(probes) == 1
    assert probes[0].bbox.x0 == 206
    assert probes[0].bbox.x1 == 302
    assert probes[0].bbox.y0 < 232
    assert probes[0].bbox.y1 > 249


def test_opencv_visual_detector_accepts_handwriting_in_focused_signature_probe() -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    image = np.full((120, 188, 3), 255, dtype=np.uint8)
    for offset in range(45):
        image[25 + offset : 29 + offset, 55 + offset : 72 + offset] = [20, 20, 20]
    region = SigningRegion(
        region_id="SR-13-1",
        page_no=13,
        bbox=BBox(x0=60, y0=72, x1=544, y1=551),
        confidence=0.9,
    )
    probe_bbox = BBox(x0=73.5, y0=206, x1=261.5, y1=326)

    detection = OpenCvVisualSignatureDetector()._detect_signature(region, image, render_bbox=probe_bbox)

    assert detection is not None
    assert detection.label == "signature"
    assert detection.confidence >= 0.6
    assert detection.bbox.y0 >= probe_bbox.y0


def test_opencv_visual_detector_rejects_printed_label_leak_at_probe_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(
        detector,
        "_visual_metrics",
        lambda _image: {
            "handwriting_dark_pixel_ratio": 0.01705,
            "handwriting_long_stroke_ratio": 0.0,
            "handwriting_bbox_x0_ratio": 0.0,
            "handwriting_bbox_x1_ratio": 0.28,
            "handwriting_bbox_y0_ratio": 0.08,
            "handwriting_bbox_y1_ratio": 0.79,
        },
    )

    detection = detector._detect_signature(
        _region(),
        object(),
        render_bbox=BBox(x0=230, y0=245, x1=302, y1=316),
    )

    assert detection is None


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
