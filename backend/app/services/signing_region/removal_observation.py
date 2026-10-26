from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from app.models import BBox
from app.services.field_observation import ObservationReason, ObservationState
from app.services.signing_region.models import SigningElement, SigningElementType, SigningRegion


BUSINESS_REMOVAL_FIELD_KEYS = {
    "authorized_representative",
    "legal_representative",
    "signature",
}


class SigningRemovalObservationDetector:
    """Find high-confidence opaque covers over a missing signing-person value."""

    render_scale = 3.0

    def enrich(
        self,
        compare_pdf: str | Path,
        matches: list[tuple[SigningRegion | None, SigningRegion | None, float]],
    ) -> list[dict[str, Any]]:
        path = Path(compare_pdf)
        if not path.is_file():
            return []
        diagnostics: list[dict[str, Any]] = []
        try:
            document = fitz.open(path)
        except Exception:
            return []
        try:
            for original, compare, score in matches:
                if (
                    original is None
                    or compare is None
                    or score < 0.7
                    or not 1 <= compare.page_no <= len(document)
                ):
                    continue
                diagnostics.extend(self._enrich_match(document, original, compare))
        finally:
            document.close()
        return diagnostics

    def _enrich_match(
        self,
        document: fitz.Document,
        original: SigningRegion,
        compare: SigningRegion,
    ) -> list[dict[str, Any]]:
        compare_slots = {
            self._slot(element)
            for element in compare.elements
            if element.element_type == SigningElementType.FIELD
        }
        diagnostics: list[dict[str, Any]] = []
        for element in original.elements:
            slot = self._slot(element)
            if (
                element.element_type != SigningElementType.FIELD
                or slot in compare_slots
                or slot[0] in {"", "unknown"}
                or slot[1] not in BUSINESS_REMOVAL_FIELD_KEYS
                or not element.text.strip()
                or not (element.raw_ref.get("value_bbox") or element.raw_ref.get("value_bboxes"))
            ):
                continue
            source_bbox = self._source_bbox(element)
            if source_bbox is None:
                continue
            projected = self._project_bbox(source_bbox, original.bbox, compare.bbox)
            evidence = self._uniform_opaque_cover(document[compare.page_no - 1], projected)
            if evidence is None:
                continue
            observation = {
                "state": ObservationState.UNOBSERVED.value,
                "reason": ObservationReason.BUSINESS_CONTENT_REMOVED_OR_COVERED.value,
                "evidence": evidence,
            }
            element.raw_ref["missing_compare_observation"] = observation
            diagnostics.append(
                {
                    "page": compare.page_no,
                    "party": slot[0],
                    "field": slot[1],
                    "original_element_id": element.element_id,
                    "observation": observation,
                }
            )
        return diagnostics

    @staticmethod
    def _slot(element: SigningElement) -> tuple[str, str]:
        return (
            str(element.raw_ref.get("party_role") or "unknown"),
            str(element.raw_ref.get("field_key") or ""),
        )

    @staticmethod
    def _source_bbox(element: SigningElement) -> BBox | None:
        payload = element.raw_ref.get("source_bbox")
        if payload:
            return BBox.model_validate(payload)
        if element.bbox.x1 > element.bbox.x0 and element.bbox.y1 > element.bbox.y0:
            return element.bbox
        return None

    @staticmethod
    def _project_bbox(source: BBox, original_region: BBox, compare_region: BBox) -> BBox:
        original_width = max(1.0, original_region.x1 - original_region.x0)
        original_height = max(1.0, original_region.y1 - original_region.y0)
        compare_width = compare_region.x1 - compare_region.x0
        compare_height = compare_region.y1 - compare_region.y0
        return BBox(
            x0=compare_region.x0 + (source.x0 - original_region.x0) / original_width * compare_width,
            y0=compare_region.y0 + (source.y0 - original_region.y0) / original_height * compare_height,
            x1=compare_region.x0 + (source.x1 - original_region.x0) / original_width * compare_width,
            y1=compare_region.y0 + (source.y1 - original_region.y0) / original_height * compare_height,
        )

    def _uniform_opaque_cover(self, page: fitz.Page, bbox: BBox) -> dict[str, Any] | None:
        clip = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1) & page.rect
        if clip.is_empty or clip.is_infinite or clip.width < 40.0 or clip.height < 8.0:
            return None
        try:
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(self.render_scale, self.render_scale),
                clip=clip,
                colorspace=fitz.csRGB,
                alpha=False,
            )
        except Exception:
            return None
        if pixmap.width < 24 or pixmap.height < 12 or pixmap.n != 3:
            return None

        histograms = [[0] * 256 for _ in range(3)]
        dark_pixels = 0
        pixel_count = pixmap.width * pixmap.height
        samples = pixmap.samples
        for index in range(0, len(samples) - 2, 3):
            red, green, blue = samples[index : index + 3]
            histograms[0][red] += 1
            histograms[1][green] += 1
            histograms[2][blue] += 1
            if red + green + blue < 660:
                dark_pixels += 1

        percentiles = [
            [self._percentile(histogram, pixel_count, quantile) for quantile in (0.05, 0.5, 0.95)]
            for histogram in histograms
        ]
        medians = [values[1] for values in percentiles]
        channel_spans = [values[2] - values[0] for values in percentiles]
        brightness = sum(medians) / 3
        chroma = max(medians) - min(medians)
        dark_ratio = dark_pixels / max(1, pixel_count)

        # A visible value cannot occupy a broad, almost perfectly flat, tinted
        # rectangle. Requiring tint keeps an ordinary white empty ROI from
        # being promoted solely because OCR returned no text.
        if brightness < 220 or chroma < 5 or max(channel_spans) > 10 or dark_ratio > 0.003:
            return None
        return {
            "method": "UNIFORM_TINTED_OPAQUE_COVER",
            "bbox": BBox(x0=clip.x0, y0=clip.y0, x1=clip.x1, y1=clip.y1).model_dump(),
            "median_rgb": medians,
            "channel_spans_p05_p95": channel_spans,
            "dark_pixel_ratio": round(dark_ratio, 6),
        }

    @staticmethod
    def _percentile(histogram: list[int], total: int, quantile: float) -> int:
        target = max(1, int(total * quantile))
        cumulative = 0
        for value, count in enumerate(histogram):
            cumulative += count
            if cumulative >= target:
                return value
        return 255
