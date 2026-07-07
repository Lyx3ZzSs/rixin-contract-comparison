from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

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
