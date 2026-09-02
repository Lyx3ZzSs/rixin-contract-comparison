from __future__ import annotations

import tempfile
import unicodedata
from pathlib import Path

import fitz

from app.models import BBox
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.signing_region.models import SigningElement, SigningElementType, SigningRegion


TARGETED_FIELD_KEYS = {"authorized_representative", "date", "legal_representative", "signature"}


class SigningFieldValueOCR:
    """Re-read one already-attributed signing value ROI with the existing OCR engine."""

    render_scale = 4.0

    def __init__(self, extractor: PPOCRV5Extractor | None = None) -> None:
        self.extractor = extractor or PPOCRV5Extractor()

    def enrich(self, pdf_path: str | Path, regions: list[SigningRegion]) -> list[dict[str, object]]:
        candidates = [
            element
            for region in regions
            for element in region.elements
            if self._eligible(element)
        ]
        if not candidates:
            return []

        diagnostics: list[dict[str, object]] = []
        with fitz.open(pdf_path) as document:
            for element in candidates:
                diagnostics.append(self._enrich_element(document, element))
        return diagnostics

    @classmethod
    def _eligible(cls, element: SigningElement) -> bool:
        raw_ref = element.raw_ref
        accepted = raw_ref.get("value_evidence") or []
        supporting = raw_ref.get("supporting_unlocalized_evidence") or []
        if (
            element.element_type != SigningElementType.FIELD
            or raw_ref.get("field_key") not in TARGETED_FIELD_KEYS
            or not element.text.strip()
            or len(accepted) != 1
            or not supporting
            or not raw_ref.get("value_roi")
            or raw_ref.get("rejected_contaminated_evidence")
        ):
            return False
        primary = cls._compact(element.text)
        return any(
            cls._is_incomplete_variant(primary, cls._compact(str(item.get("candidate_value") or "")))
            for item in supporting
            if isinstance(item, dict)
        )

    def _enrich_element(self, document: fitz.Document, element: SigningElement) -> dict[str, object]:
        raw_ref = element.raw_ref
        roi = BBox.model_validate(raw_ref["value_roi"])
        diagnostic: dict[str, object] = {
            "page": element.page_no,
            "party": raw_ref.get("party_role"),
            "field": raw_ref.get("field_key"),
            "value_roi": roi.model_dump(),
            "before": element.text,
            "status": "NOT_RUN",
        }
        if element.page_no < 1 or element.page_no > len(document):
            diagnostic["status"] = "INVALID_PAGE"
            raw_ref["targeted_roi_ocr"] = diagnostic
            return diagnostic

        page = document[element.page_no - 1]
        clip = fitz.Rect(roi.x0, roi.y0, roi.x1, roi.y1) & page.rect
        if clip.is_empty or clip.is_infinite:
            diagnostic["status"] = "INVALID_ROI"
            raw_ref["targeted_roi_ocr"] = diagnostic
            return diagnostic

        try:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(self.render_scale, self.render_scale), clip=clip, alpha=False)
            with tempfile.NamedTemporaryFile(suffix=".png") as image:
                pixmap.save(image.name)
                payload = self.extractor.predict(image.name, file_type=1)
                recognized = self.extractor.payload_to_document(payload, image.name)
        except Exception as exc:
            diagnostic.update(status="OCR_ERROR", error=type(exc).__name__)
            raw_ref["targeted_roi_ocr"] = diagnostic
            return diagnostic

        blocks = [block for result_page in recognized.pages for block in result_page.blocks if block.text.strip()]
        diagnostic["recognized"] = [block.text for block in blocks]
        supporting_values = {
            self._compact(str(item.get("candidate_value") or ""))
            for item in raw_ref.get("supporting_unlocalized_evidence") or []
            if isinstance(item, dict)
        }
        matched = next((block for block in blocks if self._compact(block.text) in supporting_values), None)
        if matched is None:
            diagnostic["status"] = "NO_SUPPORTING_MATCH"
            raw_ref["targeted_roi_ocr"] = diagnostic
            return diagnostic

        localized_bbox = self._map_crop_bbox(
            matched.bbox,
            clip=clip,
            image_width=max(1, pixmap.width),
            image_height=max(1, pixmap.height),
        )
        localized_value = matched.text.strip()
        localized_evidence = {
            "source": "targeted_roi_ppocrv5",
            "text": localized_value,
            "raw_text": localized_value,
            "normalized_text": self._compact(localized_value),
            "page_no": element.page_no,
            "bbox": localized_bbox.model_dump(),
            "confidence": matched.confidence,
            "field_key": raw_ref.get("field_key"),
            "party_role": raw_ref.get("party_role"),
            "localized": True,
            "provenance_valid": True,
            "localization_method": "TARGETED_VALUE_ROI_OCR",
        }
        raw_ref.setdefault("value_evidence", []).append(localized_evidence)
        raw_ref["value_bbox"] = localized_bbox.model_dump()
        raw_ref["display_value_bbox"] = localized_bbox.model_dump()
        raw_ref["content_recognition_status"] = "NEEDS_REVIEW"
        raw_ref["content_recognition_reason"] = "TARGETED_ROI_OCR_WITH_UNLOCALIZED_SUPPORT"
        element.text = localized_value
        diagnostic.update(status="LOCALIZED", after=localized_value, localized_bbox=localized_bbox.model_dump())
        raw_ref["targeted_roi_ocr"] = diagnostic
        return diagnostic

    @staticmethod
    def _map_crop_bbox(bbox: BBox, *, clip: fitz.Rect, image_width: int, image_height: int) -> BBox:
        scale_x = clip.width / image_width
        scale_y = clip.height / image_height
        return BBox(
            x0=clip.x0 + bbox.x0 * scale_x,
            y0=clip.y0 + bbox.y0 * scale_y,
            x1=clip.x0 + bbox.x1 * scale_x,
            y1=clip.y0 + bbox.y1 * scale_y,
        )

    @staticmethod
    def _is_incomplete_variant(primary: str, supporting: str) -> bool:
        return bool(primary and supporting and primary != supporting and primary in supporting)

    @staticmethod
    def _compact(text: str) -> str:
        return "".join(unicodedata.normalize("NFKC", text or "").split())
