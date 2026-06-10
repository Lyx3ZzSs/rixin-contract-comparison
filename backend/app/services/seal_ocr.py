"""Seal text recognition service.

Inspired by MinerU's seal OCR pipeline (``batch_analyze.py`` lines 873-918):
1. Layout detection identifies seal regions with bounding boxes.
2. Each seal region is cropped from the page image.
3. A dedicated OCR call extracts text from the cropped seal image.

This service performs step 2-3 using the same PP-OCRv5 API endpoint
that powers the main extraction pipeline, but with image-mode input
(cropped seal PNG) instead of full PDF input.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import fitz
import httpx

from app.clients import HttpClientProvider, default_http_client_provider
from app.config import settings
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.services.extractors.base import DocumentExtractionError
from app.services.models.seal_detector import SealRegion

logger = logging.getLogger(__name__)


class SealOCRService:
    """Recognize text within seal/stamp regions using PP-OCRv5."""

    def __init__(
        self,
        client_provider: HttpClientProvider = default_http_client_provider,
        artifact_store: ArtifactStore = default_artifact_store,
    ) -> None:
        self._client_provider = client_provider
        self._artifact_store = artifact_store

    def recognize_seals(
        self,
        pdf_path: Path,
        seal_regions: list[SealRegion],
        task_id: str | None = None,
    ) -> list[SealRegion]:
        """Run OCR on each seal region and update ``region.text``."""
        if not seal_regions:
            return seal_regions

        doc = fitz.open(pdf_path)
        try:
            for region in seal_regions:
                if not region.text.strip():
                    region.text = self._ocr_region(doc, region)
        finally:
            doc.close()

        if seal_regions:
            logger.info(
                "Seal OCR completed for %d regions: %s",
                len(seal_regions),
                [r.text[:30] for r in seal_regions],
            )
        return seal_regions

    def _ocr_region(self, doc: fitz.Document, region: SealRegion) -> str:
        page_idx = region.page_number - 1
        if page_idx < 0 or page_idx >= len(doc):
            return ""

        page = doc[page_idx]
        bbox = region.bbox
        clip = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
        if clip.is_empty or clip.is_infinite:
            return ""

        try:
            pix = page.get_pixmap(clip=clip)
        except Exception:
            logger.debug("Failed to crop seal region on page %d", region.page_number, exc_info=True)
            return ""

        return self._call_ocr_api(pix)

    def _call_ocr_api(self, pixmap: fitz.Pixmap) -> str:
        url = self._ocr_url()
        png_bytes = pixmap.tobytes("png")
        encoded = base64.b64encode(png_bytes).decode("ascii")

        body: dict[str, Any] = {
            "file": encoded,
            "fileType": 1,  # 1 = image (not PDF)
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "textRecScoreThresh": 0.3,
            "returnWordBox": False,
            "visualize": False,
        }
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.ppocrv5_access_token:
            headers["Authorization"] = f"Bearer {settings.ppocrv5_access_token}"

        try:
            client = self._client_provider.get_ocr_client()
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException, ValueError) as exc:
            logger.debug("Seal OCR API call failed: %s", exc)
            return ""

        if payload.get("errorCode") not in (0, None):
            logger.debug("Seal OCR API error: %s", payload.get("errorMsg"))
            return ""

        return self._extract_text(payload)

    @staticmethod
    def _ocr_url() -> str:
        base = settings.ppocrv5_url.strip().rstrip("/")
        if not base:
            raise DocumentExtractionError("未配置 PPOCRV5_URL，无法调用印章 OCR。")
        return base if base.endswith("/ocr") else f"{base}/ocr"

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        result = payload.get("result")
        if not isinstance(result, dict):
            return ""

        ocr_results = result.get("ocrResults") or result.get("ocr_results") or []
        texts: list[str] = []
        for item in ocr_results:
            if not isinstance(item, dict):
                continue
            pruned = item.get("prunedResult") or item.get("pruned_result") or item
            rec_texts = pruned.get("rec_texts") if isinstance(pruned, dict) else None
            if isinstance(rec_texts, list):
                texts.extend(str(t).strip() for t in rec_texts if str(t).strip())
                continue
            res = pruned.get("res") if isinstance(pruned, dict) else None
            if isinstance(res, list):
                for entry in res:
                    if isinstance(entry, dict):
                        text = entry.get("text", "")
                        if text and str(text).strip():
                            texts.append(str(text).strip())

        return " ".join(texts)
