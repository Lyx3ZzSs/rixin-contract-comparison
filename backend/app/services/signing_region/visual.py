from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.config import settings
from app.models import BBox
from app.services.signing_region.models import (
    SigningElementType,
    SigningRegion,
    VisualDetection,
    VisualDetectionResult,
)

logger = logging.getLogger(__name__)


class VisualSignatureDetector(Protocol):
    def detect(self, pdf_path: Path, regions: list[SigningRegion], task_id: str) -> VisualDetectionResult: ...


class LocalCpuVisualSignatureDetector:
    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path if model_path is not None else settings.signing_visual_local_model_path

    def detect(self, pdf_path: Path, regions: list[SigningRegion], task_id: str) -> VisualDetectionResult:
        del pdf_path, regions, task_id
        if not self.model_path:
            return VisualDetectionResult(available=False, error="local_model_not_configured")
        return VisualDetectionResult(available=False, error="local_model_unavailable")


class RemoteVisualSignatureDetector:
    def __init__(self, base_url: str | None = None, timeout: int | None = None) -> None:
        self.base_url = (base_url if base_url is not None else settings.signing_visual_detector_url).strip().rstrip("/")
        self.timeout = timeout if timeout is not None else settings.signing_visual_detector_timeout

    def detect(self, pdf_path: Path, regions: list[SigningRegion], task_id: str) -> VisualDetectionResult:
        if not self.base_url:
            return VisualDetectionResult(available=False, error="remote_url_not_configured")
        payload = {
            "task_id": task_id,
            "pdf_path": str(pdf_path),
            "regions": [
                {
                    "region_id": region.region_id,
                    "page_no": region.page_no,
                    "bbox": region.bbox.model_dump(),
                }
                for region in regions
            ],
        }
        try:
            response = httpx.post(f"{self.base_url}/detect-signatures", json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            detections: list[VisualDetection] = []
            for item in data.get("detections", []):
                bbox = item.get("bbox") or {}
                detections.append(
                    VisualDetection(
                        page_no=int(item.get("page_no", 0)),
                        bbox=BBox(**bbox),
                        label=str(item.get("label") or "signature"),
                        confidence=float(item.get("confidence") or 0.0),
                        model_name=str(data.get("model_name") or item.get("model_name") or "remote"),
                        raw_data=item,
                    )
                )
            return VisualDetectionResult(
                available=True,
                model_name=str(data.get("model_name") or "remote"),
                detections=detections,
            )
        except (httpx.HTTPError, AttributeError, TypeError, ValueError) as exc:
            logger.debug("Signing visual detector failed: %s", exc)
            return VisualDetectionResult(available=False, error="remote_call_failed")


class OpenCvVisualSignatureDetector:
    seal_probe_top_margin = 48.0
    min_red_seal_ratio = 0.002
    min_red_pixel_count = 40
    min_red_component_extent = 12
    max_red_component_aspect_ratio = 6.0

    def __init__(
        self,
        *,
        detect_red_seal: bool | None = None,
        detect_handwriting: bool | None = None,
        min_confidence: float | None = None,
    ) -> None:
        self.detect_red_seal = (
            detect_red_seal if detect_red_seal is not None else settings.signing_opencv_detect_red_seal
        )
        self.detect_handwriting = (
            detect_handwriting if detect_handwriting is not None else settings.signing_opencv_detect_handwriting
        )
        self.min_confidence = min_confidence if min_confidence is not None else settings.signing_opencv_min_confidence

    def detect(self, pdf_path: Path, regions: list[SigningRegion], task_id: str) -> VisualDetectionResult:
        del task_id
        if not pdf_path.exists():
            return VisualDetectionResult(available=False, model_name="opencv", error="pdf_missing")

        if self._dependencies() is None:
            return VisualDetectionResult(available=False, model_name="opencv", error="opencv_unavailable")

        detections: list[VisualDetection] = []
        rendered_count = 0
        for region in regions:
            image = self._render_region(pdf_path, region)
            if image is None:
                continue

            rendered_count += 1
            detection = self._detect_region(region, image, render_bbox=region.bbox, allow_handwriting=False)
            if self.detect_red_seal:
                probe_region = self._seal_probe_region(region)
                if probe_region.bbox != region.bbox:
                    probe_image = self._render_region(pdf_path, probe_region)
                    if probe_image is not None:
                        probe_detection = self._detect_region(
                            region,
                            probe_image,
                            render_bbox=probe_region.bbox,
                            allow_handwriting=False,
                        )
                        if probe_detection is not None and probe_detection.label == "seal":
                            detection = probe_detection
            if detection is not None and detection.label == "seal" and detection.confidence >= self.min_confidence:
                detections.append(detection)
            if self.detect_handwriting:
                for probe_region in self._signature_probe_regions(region):
                    probe_image = (
                        image if probe_region.bbox == region.bbox else self._render_region(pdf_path, probe_region)
                    )
                    signature = self._detect_signature(region, probe_image, render_bbox=probe_region.bbox)
                    if signature is not None and signature.confidence >= self.min_confidence:
                        detections.append(signature)

        if regions and rendered_count == 0:
            return VisualDetectionResult(available=False, model_name="opencv", error="render_failed")

        return VisualDetectionResult(available=True, model_name="opencv", detections=detections)

    def _detect_signature(
        self,
        region: SigningRegion,
        image: Any,
        *,
        render_bbox: BBox,
    ) -> VisualDetection | None:
        metrics = self._visual_metrics(image)
        dark_pixel_ratio = metrics["handwriting_dark_pixel_ratio"]
        long_stroke_ratio = metrics["handwriting_long_stroke_ratio"]
        focused_probe = (
            render_bbox != region.bbox
            and render_bbox.y0 > region.bbox.y0
            and render_bbox.y1 - render_bbox.y0 <= 140.0
        )
        focused_handwriting = (
            focused_probe
            and dark_pixel_ratio >= 0.008
            and metrics["handwriting_bbox_x1_ratio"] - metrics["handwriting_bbox_x0_ratio"] >= 0.08
            and metrics["handwriting_bbox_y1_ratio"] - metrics["handwriting_bbox_y0_ratio"] >= 0.18
        )
        if not focused_handwriting and (dark_pixel_ratio < 0.015 or long_stroke_ratio < 0.12):
            return None
        confidence = min(0.9, (0.62 if focused_handwriting else 0.5) + dark_pixel_ratio * 5.0)
        return VisualDetection(
            page_no=region.page_no,
            bbox=self._signature_detection_bbox(render_bbox, metrics),
            label="signature",
            confidence=round(confidence, 3),
            model_name="opencv",
            raw_data={
                **metrics,
                "reasons": ["dark_stroke_density"],
                "source_region_id": region.region_id,
            },
        )

    @staticmethod
    def _signature_probe_regions(region: SigningRegion) -> list[SigningRegion]:
        signature_fields = [
            element
            for element in region.elements
            if element.element_type == SigningElementType.FIELD
            and not element.text.strip()
            and (
                str(element.raw_ref.get("field_key") or "")
                in {"authorized_representative", "legal_representative", "signature"}
                or "签字" in str(element.raw_ref.get("field_label") or "")
                or "签名" in str(element.raw_ref.get("field_label") or "")
            )
        ]
        if signature_fields:
            midpoint = (region.bbox.x0 + region.bbox.x1) / 2
            probes: list[SigningRegion] = []
            for field in signature_fields:
                role = str(field.raw_ref.get("party_role") or "")
                column_x0 = region.bbox.x0 if role != "乙方" else midpoint
                column_x1 = region.bbox.x1 if role != "甲方" else midpoint
                probes.append(
                    region.model_copy(
                        update={
                            "bbox": BBox(
                                x0=max(column_x0, field.bbox.x0 - 8.0),
                                y0=field.bbox.y1,
                                x1=min(column_x1, max(field.bbox.x1 + 80.0, field.bbox.x0 + 180.0)),
                                y1=min(region.bbox.y1, field.bbox.y1 + 120.0),
                            )
                        }
                    )
                )
            return probes
        if region.region_role.value != "both_parties":
            return [region]
        midpoint = (region.bbox.x0 + region.bbox.x1) / 2
        return [
            region.model_copy(update={"bbox": region.bbox.model_copy(update={"x1": midpoint})}),
            region.model_copy(update={"bbox": region.bbox.model_copy(update={"x0": midpoint})}),
        ]

    def _detect_region(
        self,
        region: SigningRegion,
        image: Any,
        *,
        render_bbox: BBox | None = None,
        allow_handwriting: bool = True,
    ) -> VisualDetection | None:
        metrics = self._visual_metrics(image)
        reasons: list[str] = []
        label = ""
        confidence = 0.0

        red_pixel_ratio = metrics["red_pixel_ratio"]
        red_width = metrics["red_bbox_width"]
        red_height = metrics["red_bbox_height"]
        red_aspect_ratio = max(red_width, red_height) / max(1.0, min(red_width, red_height))
        if (
            self.detect_red_seal
            and red_pixel_ratio >= self.min_red_seal_ratio
            and metrics["red_pixel_count"] >= self.min_red_pixel_count
            and metrics["red_dominant_pixel_count"] >= self.min_red_pixel_count
            and min(red_width, red_height) >= self.min_red_component_extent
            and red_aspect_ratio <= self.max_red_component_aspect_ratio
        ):
            label = "seal"
            confidence = min(0.95, 0.55 + red_pixel_ratio * 25.0)
            reasons.append("red_seal_pixels")

        dark_pixel_ratio = metrics["handwriting_dark_pixel_ratio"]
        long_stroke_ratio = metrics["handwriting_long_stroke_ratio"]
        if allow_handwriting and self.detect_handwriting and dark_pixel_ratio >= 0.015 and long_stroke_ratio >= 0.12:
            handwriting_confidence = min(0.9, 0.5 + dark_pixel_ratio * 5.0)
            if handwriting_confidence > confidence:
                label = "signature"
                confidence = handwriting_confidence
            reasons.append("dark_stroke_density")

        if not label:
            return None

        detection_bbox = region.bbox
        if label == "seal":
            detection_bbox = self._seal_detection_bbox(region.bbox, render_bbox or region.bbox, metrics)
        return VisualDetection(
            page_no=region.page_no,
            bbox=detection_bbox,
            label=label,
            confidence=round(confidence, 3),
            model_name="opencv",
            raw_data={
                **metrics,
                "reasons": reasons,
                "source_region_id": region.region_id,
            },
        )

    @staticmethod
    def _visual_metrics(image: Any) -> dict[str, float]:
        deps = OpenCvVisualSignatureDetector._dependencies()
        if deps is None or image is None or getattr(image, "size", 0) == 0:
            return OpenCvVisualSignatureDetector._empty_visual_metrics()

        cv2, np = deps
        try:
            height, width = image.shape[:2]
            total_pixels = float(height * width)
            if total_pixels <= 0:
                return OpenCvVisualSignatureDetector._empty_visual_metrics()

            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            red_mask_low = cv2.inRange(hsv, np.array([0, 70, 50]), np.array([12, 255, 255]))
            red_mask_high = cv2.inRange(hsv, np.array([170, 70, 50]), np.array([180, 255, 255]))
            red_mask = cv2.bitwise_or(red_mask_low, red_mask_high)
            red_y, red_x = np.nonzero(red_mask)
            red_pixel_count = int(red_x.size)
            red_pixel_ratio = float(red_pixel_count) / total_pixels
            red_component_count, red_component = OpenCvVisualSignatureDetector._dominant_red_component(
                red_mask,
                cv2,
            )
            if red_component is not None:
                component_x, component_y, component_width, component_height, component_area = red_component
                red_bbox_x0_ratio = float(component_x) / width
                red_bbox_y0_ratio = float(component_y) / height
                red_bbox_x1_ratio = float(component_x + component_width) / width
                red_bbox_y1_ratio = float(component_y + component_height) / height
                red_bbox_width = float(component_width)
                red_bbox_height = float(component_height)
                red_dominant_pixel_count = float(component_area)
            else:
                red_bbox_x0_ratio = red_bbox_y0_ratio = 0.0
                red_bbox_x1_ratio = red_bbox_y1_ratio = 0.0
                red_bbox_width = red_bbox_height = 0.0
                red_dominant_pixel_count = 0.0

            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            dark_mask = gray < 80
            dark_pixel_ratio = float(np.count_nonzero(dark_mask)) / total_pixels
            long_stroke_ratio = OpenCvVisualSignatureDetector._longest_dark_stroke_ratio(dark_mask)
            horizontal_rules = np.count_nonzero(dark_mask, axis=1) >= width * 0.75
            vertical_rules = np.count_nonzero(dark_mask, axis=0) >= height * 0.75
            rule_mask = np.logical_or(horizontal_rules[:, None], vertical_rules[None, :])
            handwriting_mask = np.logical_and.reduce((dark_mask, np.logical_not(rule_mask), red_mask == 0))
            handwriting_dark_pixel_ratio = float(np.count_nonzero(handwriting_mask)) / total_pixels
            handwriting_long_stroke_ratio = OpenCvVisualSignatureDetector._longest_dark_stroke_ratio(handwriting_mask)
            handwriting_component_count, handwriting_component = OpenCvVisualSignatureDetector._dominant_red_component(
                handwriting_mask.astype("uint8"), cv2
            )
            handwriting_bbox = OpenCvVisualSignatureDetector._meaningful_component_bbox(
                handwriting_mask.astype("uint8"),
                cv2,
                min_area=max(4, int(total_pixels * 0.00035)),
            )
            if handwriting_bbox is not None:
                handwriting_x, handwriting_y, handwriting_width, handwriting_height = handwriting_bbox
                handwriting_bbox_x0_ratio = float(handwriting_x) / width
                handwriting_bbox_y0_ratio = float(handwriting_y) / height
                handwriting_bbox_x1_ratio = float(handwriting_x + handwriting_width) / width
                handwriting_bbox_y1_ratio = float(handwriting_y + handwriting_height) / height
            else:
                handwriting_bbox_x0_ratio = handwriting_bbox_y0_ratio = 0.0
                handwriting_bbox_x1_ratio = handwriting_bbox_y1_ratio = 0.0
            axis_rule_pixel_ratio = float(np.count_nonzero(np.logical_and(dark_mask, rule_mask))) / total_pixels
        except Exception:
            return OpenCvVisualSignatureDetector._empty_visual_metrics()

        return {
            "red_pixel_ratio": red_pixel_ratio,
            "red_pixel_count": float(red_pixel_count),
            "red_component_count": float(red_component_count),
            "red_dominant_pixel_count": red_dominant_pixel_count,
            "red_bbox_x0_ratio": red_bbox_x0_ratio,
            "red_bbox_y0_ratio": red_bbox_y0_ratio,
            "red_bbox_x1_ratio": red_bbox_x1_ratio,
            "red_bbox_y1_ratio": red_bbox_y1_ratio,
            "red_bbox_width": red_bbox_width,
            "red_bbox_height": red_bbox_height,
            "dark_pixel_ratio": dark_pixel_ratio,
            "long_stroke_ratio": long_stroke_ratio,
            "handwriting_dark_pixel_ratio": handwriting_dark_pixel_ratio,
            "handwriting_long_stroke_ratio": handwriting_long_stroke_ratio,
            "handwriting_component_count": float(handwriting_component_count),
            "handwriting_bbox_x0_ratio": handwriting_bbox_x0_ratio,
            "handwriting_bbox_y0_ratio": handwriting_bbox_y0_ratio,
            "handwriting_bbox_x1_ratio": handwriting_bbox_x1_ratio,
            "handwriting_bbox_y1_ratio": handwriting_bbox_y1_ratio,
            "axis_rule_pixel_ratio": axis_rule_pixel_ratio,
        }

    @staticmethod
    def _empty_visual_metrics() -> dict[str, float]:
        return {
            "red_pixel_ratio": 0.0,
            "red_pixel_count": 0.0,
            "red_component_count": 0.0,
            "red_dominant_pixel_count": 0.0,
            "red_bbox_x0_ratio": 0.0,
            "red_bbox_y0_ratio": 0.0,
            "red_bbox_x1_ratio": 0.0,
            "red_bbox_y1_ratio": 0.0,
            "red_bbox_width": 0.0,
            "red_bbox_height": 0.0,
            "dark_pixel_ratio": 0.0,
            "long_stroke_ratio": 0.0,
            "handwriting_dark_pixel_ratio": 0.0,
            "handwriting_long_stroke_ratio": 0.0,
            "handwriting_component_count": 0.0,
            "handwriting_bbox_x0_ratio": 0.0,
            "handwriting_bbox_y0_ratio": 0.0,
            "handwriting_bbox_x1_ratio": 0.0,
            "handwriting_bbox_y1_ratio": 0.0,
            "axis_rule_pixel_ratio": 0.0,
        }

    def _seal_probe_region(self, region: SigningRegion) -> SigningRegion:
        return region.model_copy(
            update={
                "bbox": region.bbox.model_copy(update={"y0": max(0.0, region.bbox.y0 - self.seal_probe_top_margin)})
            }
        )

    @staticmethod
    def _dominant_red_component(red_mask: Any, cv2: Any) -> tuple[int, tuple[int, int, int, int, int] | None]:
        component_count, _, stats, _ = cv2.connectedComponentsWithStats(red_mask, connectivity=8)
        if component_count <= 1:
            return 0, None
        components = [tuple(int(value) for value in stats[index]) for index in range(1, component_count)]
        return len(components), max(components, key=lambda component: component[4])

    @staticmethod
    def _meaningful_component_bbox(mask: Any, cv2: Any, *, min_area: int) -> tuple[int, int, int, int] | None:
        component_count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        components = [
            tuple(int(value) for value in stats[index])
            for index in range(1, component_count)
            if int(stats[index][4]) >= min_area
        ]
        if not components:
            return None
        x0 = min(component[0] for component in components)
        y0 = min(component[1] for component in components)
        x1 = max(component[0] + component[2] for component in components)
        y1 = max(component[1] + component[3] for component in components)
        return x0, y0, x1 - x0, y1 - y0

    def _seal_detection_bbox(
        self,
        region_bbox: BBox,
        render_bbox: BBox,
        metrics: dict[str, float],
    ) -> BBox:
        if metrics.get("red_pixel_count", 0.0) <= 0:
            return region_bbox
        width = max(0.0, render_bbox.x1 - render_bbox.x0)
        height = max(0.0, render_bbox.y1 - render_bbox.y0)
        red_bbox = BBox(
            x0=max(0.0, render_bbox.x0 + width * metrics["red_bbox_x0_ratio"]),
            y0=max(0.0, render_bbox.y0 + height * metrics["red_bbox_y0_ratio"]),
            x1=render_bbox.x0 + width * metrics["red_bbox_x1_ratio"],
            y1=render_bbox.y0 + height * metrics["red_bbox_y1_ratio"],
        )
        return red_bbox

    @staticmethod
    def _signature_detection_bbox(render_bbox: BBox, metrics: dict[str, float]) -> BBox:
        if metrics.get("handwriting_component_count", 0.0) <= 0:
            return render_bbox
        width = max(0.0, render_bbox.x1 - render_bbox.x0)
        height = max(0.0, render_bbox.y1 - render_bbox.y0)
        return BBox(
            x0=render_bbox.x0 + width * metrics["handwriting_bbox_x0_ratio"],
            y0=render_bbox.y0 + height * metrics["handwriting_bbox_y0_ratio"],
            x1=render_bbox.x0 + width * metrics["handwriting_bbox_x1_ratio"],
            y1=render_bbox.y0 + height * metrics["handwriting_bbox_y1_ratio"],
        )

    @staticmethod
    def _longest_dark_stroke_ratio(mask: Any) -> float:
        height, width = mask.shape[:2]
        if height <= 0 or width <= 0:
            return 0.0

        def longest_run(values: Any) -> int:
            current = 0
            longest = 0
            for value in values:
                current = current + 1 if bool(value) else 0
                longest = max(longest, current)
            return longest

        horizontal_runs = [longest_run(row) for row in mask]
        vertical_runs = [longest_run(column) for column in mask.T]
        horizontal = (
            max(horizontal_runs, default=0) / width if sum(run >= width * 0.12 for run in horizontal_runs) >= 3 else 0.0
        )
        vertical = (
            max(vertical_runs, default=0) / height if sum(run >= height * 0.30 for run in vertical_runs) >= 3 else 0.0
        )
        return float(max(horizontal, vertical))

    @staticmethod
    def _render_region(pdf_path: Path, region: SigningRegion) -> Any | None:
        deps = OpenCvVisualSignatureDetector._dependencies()
        if deps is None:
            return None

        _, np = deps
        try:
            import fitz
        except Exception:
            return None

        doc = None
        try:
            doc = fitz.open(pdf_path)
            if region.page_no < 1 or region.page_no > len(doc):
                return None

            page = doc[region.page_no - 1]
            rect = fitz.Rect(region.bbox.x0, region.bbox.y0, region.bbox.x1, region.bbox.y1)
            pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            if pix.width <= 0 or pix.height <= 0 or pix.n not in (3, 4):
                return None

            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                image = image[:, :, :3]
            return image[:, :, ::-1].copy()
        except Exception:
            return None
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    @staticmethod
    def _dependencies() -> tuple[Any, Any] | None:
        try:
            import cv2
            import numpy as np
        except Exception:
            return None
        return cv2, np


class OpenCvSigningRegionFingerprinter:
    def fingerprint_region(self, pdf_path: Path, region: SigningRegion) -> dict[str, str | float | bool]:
        if not pdf_path.exists():
            return {"status": "unavailable", "reason": "pdf_missing"}

        try:
            import fitz
        except Exception:
            return {"status": "unavailable", "reason": "pymupdf_unavailable"}

        doc = None
        try:
            doc = fitz.open(pdf_path)
            if region.page_no < 1 or region.page_no > len(doc):
                return {"status": "unavailable", "reason": "page_out_of_range"}

            page = doc[region.page_no - 1]
            rect = fitz.Rect(region.bbox.x0, region.bbox.y0, region.bbox.x1, region.bbox.y1)
            pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            payload = pix.samples
        except Exception:
            return {"status": "unavailable", "reason": "render_failed"}
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

        import hashlib

        digest = hashlib.sha256(payload).hexdigest()[:24]
        return {
            "status": "ok",
            "hash": digest,
            "visual_hash": digest,
            "width": float(pix.width),
            "height": float(pix.height),
        }
