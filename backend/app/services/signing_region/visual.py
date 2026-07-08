from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.config import settings
from app.models import BBox
from app.services.signing_region.models import SigningRegion, VisualDetection, VisualDetectionResult

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
        self.min_confidence = (
            min_confidence if min_confidence is not None else settings.signing_opencv_min_confidence
        )

    def detect(self, pdf_path: Path, regions: list[SigningRegion], task_id: str) -> VisualDetectionResult:
        del task_id
        if not pdf_path.exists():
            return VisualDetectionResult(available=False, model_name="opencv", error="pdf_missing")

        if self._dependencies() is None:
            return VisualDetectionResult(available=False, model_name="opencv", error="opencv_unavailable")

        detections: list[VisualDetection] = []
        for region in regions:
            image = self._render_region(pdf_path, region)
            if image is None:
                continue

            detection = self._detect_region(region, image)
            if detection is not None and detection.confidence >= self.min_confidence:
                detections.append(detection)

        return VisualDetectionResult(available=True, model_name="opencv", detections=detections)

    def _detect_region(self, region: SigningRegion, image: Any) -> VisualDetection | None:
        metrics = self._visual_metrics(image)
        reasons: list[str] = []
        label = ""
        confidence = 0.0

        red_pixel_ratio = metrics["red_pixel_ratio"]
        if self.detect_red_seal and red_pixel_ratio >= 0.01:
            label = "seal"
            confidence = min(0.95, 0.55 + red_pixel_ratio * 8.0)
            reasons.append("red_seal_pixels")

        dark_pixel_ratio = metrics["dark_pixel_ratio"]
        if self.detect_handwriting and dark_pixel_ratio >= 0.015:
            handwriting_confidence = min(0.9, 0.5 + dark_pixel_ratio * 5.0)
            if handwriting_confidence > confidence:
                label = "signature"
                confidence = handwriting_confidence
            reasons.append("dark_stroke_density")

        if not label:
            return None

        return VisualDetection(
            page_no=region.page_no,
            bbox=region.bbox,
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
            return {"red_pixel_ratio": 0.0, "dark_pixel_ratio": 0.0}

        cv2, np = deps
        try:
            height, width = image.shape[:2]
            total_pixels = float(height * width)
            if total_pixels <= 0:
                return {"red_pixel_ratio": 0.0, "dark_pixel_ratio": 0.0}

            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            red_mask_low = cv2.inRange(hsv, np.array([0, 70, 50]), np.array([12, 255, 255]))
            red_mask_high = cv2.inRange(hsv, np.array([170, 70, 50]), np.array([180, 255, 255]))
            red_mask = cv2.bitwise_or(red_mask_low, red_mask_high)
            red_pixel_ratio = float(np.count_nonzero(red_mask)) / total_pixels

            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            dark_pixel_ratio = float(np.count_nonzero(gray < 80)) / total_pixels
        except Exception:
            return {"red_pixel_ratio": 0.0, "dark_pixel_ratio": 0.0}

        return {
            "red_pixel_ratio": red_pixel_ratio,
            "dark_pixel_ratio": dark_pixel_ratio,
        }

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
