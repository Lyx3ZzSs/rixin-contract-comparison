from __future__ import annotations

import tempfile
import re
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

    def recover_missing_fields(self, pdf_path, region: SigningRegion | None, peer: SigningRegion | None) -> list[dict]:
        """Recall missing labels from this side's party area, never peer values."""
        if region is None or peer is None:
            return []
        import hashlib
        import numpy as np
        diagnostics = []
        own_keys = {(e.raw_ref.get("party_role"), e.raw_ref.get("field_key")) for e in region.elements}
        wanted = {(e.raw_ref.get("party_role"), e.raw_ref.get("field_key")) for e in peer.elements
                  if e.element_type == SigningElementType.FIELD and e.text.strip()
                  and e.raw_ref.get("field_key") in {"phone", "contact"}} - own_keys
        rejected = {(e.raw_ref.get("party_role"), e.raw_ref.get("field_key")) for e in region.elements
                    if e.raw_ref.get("rejected_stamp_value") and e.raw_ref.get("field_key") == "contact"}
        wanted |= rejected
        if not wanted:
            return []
        if not Path(pdf_path).is_file():
            return [{"page": region.page_no, "method": "MISSING_FIELD_LOCAL_OCR", "status": "PDF_UNAVAILABLE"}]
        with fitz.open(pdf_path) as document:
            for role in sorted({role for role, _ in wanted if role}):
                anchors = [e for e in region.elements if e.element_type == SigningElementType.PARTY_FIELD
                           and e.raw_ref.get("party_role") == role]
                if len(anchors) != 1:
                    continue
                anchor = anchors[0]
                lower = [e.bbox.y0 for e in region.elements if e.raw_ref.get("party_role") not in {None, role}
                         and e.bbox.y0 > anchor.bbox.y1 and abs(e.bbox.x0 - anchor.bbox.x0) < 80]
                right = [e.bbox.x0 - 5 for e in region.elements if e.raw_ref.get("party_role") not in {None, role}
                         and e.bbox.x0 > anchor.bbox.x1 and e.bbox.y0 < anchor.bbox.y1 + 40]
                clip = fitz.Rect(anchor.bbox.x0 - 5, anchor.bbox.y0,
                                 min(right, default=region.bbox.x1 + 5),
                                 min(lower + [anchor.bbox.y1 + 160]) - 5) & document[region.page_no - 1].rect
                diagnostic = {"page": region.page_no, "party": role, "method": "MISSING_FIELD_LOCAL_OCR",
                              "status": "NOT_RUN", "readings": [], "attempted_inputs": []}
                diagnostics.append(diagnostic)
                readings = []
                try:
                    for scale in (3.0, 5.0):
                        pix = document[region.page_no - 1].get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False, annots=False)
                        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                        cleaned = fitz.Pixmap(fitz.csGRAY, pix.width, pix.height, np.ascontiguousarray(rgb[:, :, 0]).tobytes(), False)
                        with tempfile.NamedTemporaryFile(suffix=".png") as image:
                            cleaned.save(image.name)
                            digest = hashlib.sha256(Path(image.name).read_bytes()).hexdigest()
                            diagnostic["attempted_inputs"].append({"input_sha256": digest, "render_scale": scale})
                            parsed = self.extractor.payload_to_document(self.extractor.predict(image.name, file_type=1), image.name)
                        found = {}
                        for block in sorted([b for p in parsed.pages for b in p.blocks], key=lambda b: (b.bbox.y0, b.bbox.x0)):
                            text = self._compact(block.text)
                            party = re.match(r"^(甲方|乙方|买方|卖方|设计方)[:：]", text)
                            if party and party[1] != role:
                                break
                            match = re.fullmatch(r"(电话|联系人)[:：](.+)", text)
                            if not match or block.confidence is None or block.confidence < 0.85:
                                continue
                            key = "phone" if match[1] == "电话" else "contact"
                            value = match[2].rstrip(".。") if key == "phone" else match[2]
                            if (role, key) not in wanted or (key == "phone" and not re.fullmatch(r"[\d\-－—()（）+ ]{7,20}", value)):
                                continue
                            bbox = self._map_crop_bbox(block.bbox, clip=clip, image_width=parsed.pages[0].width, image_height=parsed.pages[0].height)
                            found[key] = {"text": value, "raw_text": block.text, "bbox": bbox.model_dump(),
                                          "page_no": region.page_no, "confidence": block.confidence, "source": "missing_field_local_ppocrv5",
                                          "localized": True, "provenance_valid": True, "input_sha256": digest}
                        readings.append(found)
                        diagnostic["readings"].append(found)
                    recovered = []
                    for key in readings[0].keys() & readings[1].keys():
                        first, second = readings[0][key], readings[1][key]
                        if first["text"] != second["text"]:
                            continue
                        prior = [e for e in region.elements if (e.raw_ref.get("party_role"), e.raw_ref.get("field_key")) == (role, key)
                                 and e.raw_ref.get("rejected_stamp_value")]
                        region.elements = [e for e in region.elements if e not in prior]
                        region.elements.append(SigningElement(element_id=f"{region.region_id}-recalled-{role}-{key}",
                            element_type=SigningElementType.FIELD, page_no=region.page_no, bbox=BBox.model_validate(first["bbox"]),
                            text=first["text"], source="ocr", confidence=min(first["confidence"], second["confidence"]),
                            raw_ref={"field_key": key, "field_label": "电话" if key == "phone" else "联系人", "party_role": role,
                                     "value_evidence": [first, second], "value_bbox": first["bbox"], "value_roi": first["bbox"],
                                     "replaced_stamp_readings": [e.raw_ref['rejected_stamp_value'] for e in prior],
                                     "content_recognition_status": "CONFIRMED", "content_recognition_reason": "CONSISTENT_LOCAL_FIELD_READINGS"}))
                        recovered.append(key)
                    diagnostic.update(status="RECOVERED" if recovered else "LOCAL_READINGS_UNRESOLVED", recovered_fields=recovered)
                except Exception as exc:
                    diagnostic.update(status="CACHE_MISS" if str(exc).startswith("MissingCachedFieldOCR:") else "OCR_ERROR", error=type(exc).__name__)
        return diagnostics

    def enrich_disputed_dates(self, pdf_path, region, peer) -> list[dict]:
        if region is None or peer is None:
            return []
        from app.services.signing_region.comparator import SigningRegionComparator
        peer_dates = {e.raw_ref.get("party_role"): e.text for e in peer.elements if e.raw_ref.get("field_key") == "date"}
        candidates = [e for e in region.elements if e.raw_ref.get("field_key") == "date" and e.text.strip()
                      and e.raw_ref.get("value_roi") and peer_dates.get(e.raw_ref.get("party_role"))
                      and re.search(r"(?:19|20)\d{2}", e.text)
                      and re.search(r"(?:19|20)\d{2}", peer_dates[e.raw_ref.get("party_role")])
                      and SigningRegionComparator._normalized_field_value("date",peer_dates[e.raw_ref.get("party_role")])
                          != SigningRegionComparator._normalized_field_value("date",e.text)]
        if not candidates or not Path(pdf_path).is_file():
            return []
        with fitz.open(pdf_path) as document:
            # Repeating one recognizer at two scales is not independent
            # evidence that resolves a disputed handwritten date.
            for element in candidates:
                element.raw_ref["content_recognition_status"] = "NEEDS_REVIEW"
                element.raw_ref["content_recognition_reason"] = "DISPUTED_DATE_REQUIRES_INDEPENDENT_EVIDENCE"
            return [self._recover_occluded_printed_field(document, element) for element in candidates]

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

    def enrich_occluded_fields(self, pdf_path: str | Path, regions: list[SigningRegion], *, source_document=None) -> list[dict[str, object]]:
        """Recover printed fields from this document's red-suppressed pixels.

        A stamp is an occlusion cue, never evidence that two values are equal.
        Two independently rendered crops must agree before replacing OCR text.
        """
        candidates = []
        for region in regions:
            seals = [item for item in region.elements if item.element_type == SigningElementType.SEAL
                     and item.source == "visual_model"]
            for element in region.elements:
                if element.element_type not in {SigningElementType.FIELD, SigningElementType.PARTY_FIELD} or element.raw_ref.get("field_key") not in {
                    "party_name", "address"
                } or not element.raw_ref.get("value_roi"):
                    continue
                roi = BBox.model_validate(element.raw_ref["value_roi"])
                if element.raw_ref.get("field_key") == "address":
                    # Use the adjacent column's actual field anchor, not the
                    # midpoint of a truncated OCR line. Wrapped addresses may
                    # continue to the left of the first line's value start.
                    neighbors = [item.bbox.x0 for item in region.elements
                                 if item.element_type == SigningElementType.FIELD
                                 and item.raw_ref.get("party_role") != element.raw_ref.get("party_role")
                                 and item.bbox.x0 > element.bbox.x0 + 100
                                 and item.bbox.y0 < roi.y1 and item.bbox.y1 > roi.y0]
                    if neighbors:
                        roi = BBox(x0=element.bbox.x0, y0=roi.y0, x1=min(neighbors) - 5, y1=roi.y1)
                        element.raw_ref["value_roi"] = roi.model_dump()
                if any(seal.page_no == element.page_no and min(seal.bbox.x1, roi.x1) > max(seal.bbox.x0, roi.x0)
                       and min(seal.bbox.y1, roi.y1) > max(seal.bbox.y0, roi.y0) for seal in seals):
                    candidates.append(element)
        if not candidates:
            return []
        diagnostics = []
        with fitz.open(pdf_path) as document:
            for element in candidates:
                if source_document is not None and element.raw_ref.get("field_key") == "party_name":
                    role = element.raw_ref.get("party_role")
                    support = []
                    for p in source_document.pages:
                        if p.page_no == element.page_no:
                            continue
                        for b in p.blocks:
                            match = re.fullmatch(r"(甲方|乙方|买方|卖方|设计方)[:：](.+)", self._compact(b.text))
                            if match and match[1] == role and b.confidence is not None and b.confidence >= .95:
                                support.append({"text":match[2],"page_no":p.page_no,"bbox":b.bbox.model_dump(),
                                                "block_id":b.block_id,"source":b.source,"confidence":b.confidence})
                    element.raw_ref["same_document_party_support"] = support
                diagnostics.append(self._recover_occluded_printed_field(document, element))
        return diagnostics

    def _recover_occluded_printed_field(self, document: fitz.Document, element: SigningElement) -> dict[str, object]:
        import numpy as np

        diagnostic = {"page": element.page_no, "party": element.raw_ref.get("party_role"),
                      "field": element.raw_ref.get("field_key"), "before": element.text,
                      "method": "RED_SUPPRESSED_LOCAL_OCR", "status": "NOT_RUN", "readings": []}
        element.raw_ref["occluded_field_ocr"] = diagnostic
        if not 1 <= element.page_no <= len(document):
            diagnostic["status"] = "INVALID_PAGE"
            return diagnostic
        roi = BBox.model_validate(element.raw_ref["value_roi"])
        page = document[element.page_no - 1]
        if element.raw_ref.get("field_key") == "date":
            # Broad inferred value ROIs may include the next party's date label.
            # Re-read the independently located field line instead.
            roi = element.bbox
        clip = fitz.Rect(roi.x0, roi.y0 - 2, roi.x1, roi.y1 + 2) & page.rect
        if clip.is_empty or clip.is_infinite:
            diagnostic["status"] = "INVALID_ROI"
            return diagnostic
        readings = []
        try:
            for scale in (3.0, 5.0):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
                rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
                # Red stamp ink approaches white in the R channel; black
                # printed strokes remain dark. No characters are synthesized.
                red = np.ascontiguousarray(rgb[:, :, 0])
                cleaned = fitz.Pixmap(fitz.csGRAY, pixmap.width, pixmap.height, red.tobytes(), False)
                with tempfile.NamedTemporaryFile(suffix=".png") as image:
                    cleaned.save(image.name)
                    import hashlib
                    input_sha256 = hashlib.sha256(Path(image.name).read_bytes()).hexdigest()
                    diagnostic.setdefault("attempted_inputs", []).append({"input_sha256": input_sha256, "render_scale": scale})
                    parsed = self.extractor.payload_to_document(self.extractor.predict(image.name, file_type=1), image.name)
                blocks = [b for p in parsed.pages for b in p.blocks if b.text.strip()]
                # This is a one-row value ROI. Do not accept a collection of
                # nearby labels, signatures or multiple detected text rows.
                allow_wrapped_party = element.element_type == SigningElementType.PARTY_FIELD
                allow_wrapped_address = element.raw_ref.get("field_key") == "address"
                if allow_wrapped_party:
                    rejected = [b for b in blocks if b.confidence is not None and b.confidence < 0.5 and len(b.text.strip()) == 1]
                    diagnostic.setdefault("rejected_local_fragments", []).extend(
                        {"text": b.text, "confidence": b.confidence, "render_scale": scale} for b in rejected)
                    blocks = [b for b in blocks if b not in rejected]
                    selected = self._party_value_lines(blocks)
                    diagnostic.setdefault("excluded_neighbor_lines", []).extend(
                        {"text": b.text, "bbox": b.bbox.model_dump(), "render_scale": scale}
                        for b in blocks if b not in selected)
                    blocks = selected
                diagnostic.setdefault("detected_blocks", []).append({"render_scale": scale,
                    "blocks": [{"text": b.text, "confidence": b.confidence, "bbox": b.bbox.model_dump()} for b in blocks]})
                if not blocks or len(blocks) > (3 if allow_wrapped_address else 2 if allow_wrapped_party else 1) or any(
                    block.confidence is not None and block.confidence < 0.85 for block in blocks
                ):
                    diagnostic["status"] = "AMBIGUOUS_LOCAL_READING"
                    return diagnostic
                blocks.sort(key=lambda block: (block.bbox.y0, block.bbox.x0))
                block = blocks[0]
                value = "".join(item.text.strip() for item in blocks)
                if len(blocks) > 1:
                    for first, second in zip(blocks, blocks[1:]):
                        if (second.bbox.y0 < first.bbox.y1 - 2 or second.bbox.x0 > first.bbox.x0 + 30
                            or second.bbox.y0 - first.bbox.y1 > first.bbox.y1 - first.bbox.y0):
                            diagnostic["status"] = "AMBIGUOUS_WRAPPED_FIELD"
                            return diagnostic
                    block = block.model_copy(update={"bbox": BBox(
                        x0=min(item.bbox.x0 for item in blocks), y0=min(item.bbox.y0 for item in blocks),
                        x1=max(item.bbox.x1 for item in blocks), y1=max(item.bbox.y1 for item in blocks))})
                if allow_wrapped_address:
                    value = re.sub(r"^地址\s*[:：]\s*", "", value)
                if element.raw_ref.get("field_key") == "date":
                    value = re.sub(r"^(?:日期|时间)\s*[:：]\s*", "", value)
                    match = re.fullmatch(r"\s*(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?\s*", value)
                    if not match:
                        diagnostic["status"] = "INCOMPLETE_LOCAL_DATE"
                        element.raw_ref["content_recognition_status"] = "NEEDS_REVIEW"
                        element.raw_ref["content_recognition_reason"] = "INCOMPLETE_LOCAL_DATE"
                        element.raw_ref["targeted_roi_ocr"] = {"status": "INCOMPLETE_LOCAL_DATE"}
                        return diagnostic
                    from datetime import date
                    try:
                        actual_date = date(*map(int, match.groups()))
                    except ValueError:
                        diagnostic["status"] = "INVALID_LOCAL_DATE"
                        return diagnostic
                    value = f"{actual_date.year}年{actual_date.month}月{actual_date.day}日"
                if any(marker in value for marker in ("：", ":", "地址", "单位名称")):
                    diagnostic["status"] = "LABEL_OR_NEIGHBOR_IN_VALUE_ROI"
                    return diagnostic
                bbox = self._map_crop_bbox(block.bbox, clip=clip, image_width=parsed.pages[0].width, image_height=parsed.pages[0].height)
                reading = {"source": "red_suppressed_roi_ppocrv5", "text": value,
                           "raw_text": value, "normalized_text": self._compact(value),
                           "page_no": element.page_no, "bbox": bbox.model_dump(), "render_scale": scale,
                           "input_sha256": input_sha256,
                           "confidence": block.confidence, "field_key": element.raw_ref.get("field_key"),
                           "party_role": element.raw_ref.get("party_role"), "localized": True, "provenance_valid": True,
                           "localization_method": "RED_SUPPRESSED_VALUE_ROI_OCR"}
                readings.append(reading)
                diagnostic["readings"].append(reading)
        except Exception as exc:
            diagnostic.update(status="CACHE_MISS" if str(exc).startswith("MissingCachedFieldOCR:") else "OCR_ERROR",
                              error=type(exc).__name__)
            return diagnostic
        values = {item["normalized_text"] for item in readings}
        if len(values) != 1 or len(next(iter(values), "")) < 6:
            diagnostic["status"] = "LOCAL_READINGS_DISAGREE"
            return diagnostic
        value = readings[0]["text"]
        old = self._compact(element.text)
        if element.element_type == SigningElementType.PARTY_FIELD:
            old = re.sub(r"^(?:甲方|乙方|丙方|丁方|买方|卖方|设计方)[:：]", "", old)
        normalized = self._compact(value)
        repairing_contaminated_address = (element.raw_ref.get("field_key") == "address"
            and bool(element.raw_ref.get("rejected_contaminated_evidence"))
            and len(old) >= 6 and normalized.startswith(old[:6]))
        corroborated_party = (element.raw_ref.get("field_key") == "party_name"
            and element.raw_ref.get("source_block_type") in {"seal", "seal_region"}
            and {self._compact(s['text']) for s in element.raw_ref.get("same_document_party_support", [])} == {normalized})
        if old and not self._ordered_fragment(old, normalized) and not repairing_contaminated_address and not corroborated_party:
            diagnostic["status"] = "LOCAL_READING_CONFLICTS_WITH_EXISTING_VALUE"
            return diagnostic
        if corroborated_party:
            diagnostic["corroboration"] = element.raw_ref["same_document_party_support"]
        if element.raw_ref.get("field_key") == "party_name" and not any(
            suffix in normalized for suffix in ("公司", "中心", "厂", "院", "局")
        ):
            diagnostic["status"] = "INCOMPLETE_PARTY_NAME"
            return diagnostic
        element.text = (f"{element.raw_ref.get('party_role')}：{value}"
                        if element.element_type == SigningElementType.PARTY_FIELD else value)
        element.raw_ref["superseded_value_evidence"] = element.raw_ref.get("value_evidence", [])
        element.raw_ref["value_evidence"] = readings
        element.raw_ref["value_bbox"] = readings[0]["bbox"]
        element.raw_ref["display_value_bbox"] = readings[0]["bbox"]
        element.raw_ref["value_bboxes"] = [readings[0]["bbox"]]
        prior_content_status = element.raw_ref.get("content_recognition_status")
        prior_content_reason = element.raw_ref.get("content_recognition_reason")
        unresolved_date = element.raw_ref.get("field_key") == "date" and prior_content_status == "NEEDS_REVIEW"
        # Two render scales use the same recognizer. They can recover a date
        # candidate, but cannot by themselves resolve an existing content review.
        element.raw_ref["content_recognition_status"] = "NEEDS_REVIEW" if unresolved_date else "CONFIRMED"
        element.raw_ref["content_recognition_reason"] = (
            str(prior_content_reason or "DATE_CONTENT_REVIEW_PRESERVED")
            if unresolved_date else "CONSISTENT_RED_SUPPRESSED_LOCAL_READINGS"
        )
        diagnostic["prior_content_recognition_status"] = prior_content_status
        diagnostic["content_review_preserved"] = unresolved_date
        if element.raw_ref.get("field_key") == "date":
            parts = re.fullmatch(r"(\d+)年(\d+)月(\d+)日", value)
            element.raw_ref["date_components"] = dict(zip(("year", "month", "day"), parts.groups()))
            element.raw_ref["date_component_bboxes"] = {}
        # Preserve the occlusion diagnosis, but a successful independent local
        # re-read establishes an observed value even if the peer is different.
        from app.services.field_observation import ObservationState
        element.observation_state = ObservationState.OBSERVED_VALUE
        element.observation_reason = "CONSISTENT_RED_SUPPRESSED_LOCAL_READINGS"
        element.raw_ref["observation_state"] = element.observation_state.value
        element.raw_ref["observation_reason"] = element.observation_reason
        diagnostic.update(status="RECOVERED", after=value)
        return diagnostic

    @classmethod
    def _ordered_fragment(cls, fragment: str, value: str) -> bool:
        iterator = iter(value)
        return all(character in iterator for character in fragment)

    @staticmethod
    def _party_value_lines(blocks):
        """Select a complete company line before rejecting a broad value crop.

        Do not choose between competing company readings. Keep the original
        multi-line validation for wrapped or incomplete company names.
        """
        companies = [b for b in blocks if re.fullmatch(
            r"[^:：\n]{2,80}(?:有限公司|股份公司|设计院|研究院)", b.text.strip())]
        neighbors = [b for b in blocks if re.search(r"签字|签名|代表|电话|联系人", b.text)
                     or re.fullmatch(r"[\d\s+()-]{5,}", b.text.strip())]
        if len(companies) == 1 and len(companies) + len(neighbors) == len(blocks):
            return companies
        return blocks

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
