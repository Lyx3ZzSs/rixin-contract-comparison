from __future__ import annotations

import re
from app.models import DiffItem, Document


def retain_recognition_evidence(diffs: list[DiffItem], original: Document, compare: Document) -> None:
    """Carry source disagreement into content status independently of diff quality."""
    for diff in diffs:
        observations = []
        disputed = False
        confidences = []
        for side, document, evidences in [
            ("original", original, diff.original_evidence),
            ("compare", compare, diff.compare_evidence),
        ]:
            for page in document.pages:
                for block in page.blocks:
                    if not any(e.page_no == page.page_no and e.bbox == block.bbox for e in evidences):
                        continue
                    readings = [r.model_dump(mode="json") for r in block.source_evidence]

                    # Layout engines may return a whole paragraph while OCR returns one
                    # line. Only comparable physical crops can disagree as readings.
                    def comparable(reading):
                        a, b = block.bbox, reading.bbox
                        intersection = max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(
                            0, min(a.y1, b.y1) - max(a.y0, b.y0)
                        )
                        union = (a.x1 - a.x0) * (a.y1 - a.y0) + (b.x1 - b.x0) * (b.y1 - b.y0) - intersection
                        return intersection / max(1, union) >= 0.6

                    normalized = {
                        re.sub(r"\s+", "", r.raw_text)
                        for r in block.source_evidence
                        if r.raw_text.strip() and comparable(r)
                    }
                    local_date = "CONSISTENT_LOCAL_COVER_DATE" in block.semantic_reasons
                    risk = (len(normalized) > 1 and not local_date) or "UNRESOLVED_COVER_DATE" in block.semantic_reasons
                    disputed |= risk
                    if block.confidence is not None:
                        confidences.append(block.confidence)
                    if readings or risk:
                        observations.append(
                            {
                                "side": side,
                                "block_id": block.block_id,
                                "readings": readings,
                                "reasons": block.semantic_reasons,
                                "content_disputed": risk,
                            }
                        )
        # HeaderFooterComparator already groups short repeated footer values.
        # Preserve strong document-level consensus instead of letting one noisy
        # page invalidate the shared value (never apply this to unique headers).
        if diff.source_type == "header_footer" and diff.title == "页脚":
            for side, value in (("original", diff.original_text), ("compare", diff.compare_text)):
                same_side = [o for o in observations if o["side"] == side]
                compact = re.sub(r"\s+", "", value)
                agreeing = [
                    o
                    for o in same_side
                    if len(o["readings"]) >= 2
                    and all(re.sub(r"\s+", "", r["raw_text"]) == compact for r in o["readings"])
                ]
                if 0 < len(compact) <= 8 and len(agreeing) >= 3 and len(agreeing) / max(1, len(same_side)) >= 0.8:
                    for observation in same_side:
                        observation["content_disputed"] = False
                    diff.match_score_details.setdefault("repeated_footer_consensus", {})[side] = {
                        "agreeing_pages": len(agreeing),
                        "observed_pages": len(same_side),
                        "text": value,
                    }
            disputed = any(o["content_disputed"] for o in observations)
        if observations:
            diff.match_score_details["recognition_evidence"] = observations
        if confidences:
            diff.text_confidence = min(confidences)
        if disputed:
            diff.content_recognition_status = "NEEDS_REVIEW"
            diff.content_recognition_reason = "SOURCE_READINGS_DISAGREE_OR_DATE_UNRESOLVED"
            diff.quality_status = "NEEDS_REVIEW"
            diff.review_flags = list(dict.fromkeys([*diff.review_flags, "CONTENT_RECOGNITION_REVIEW"]))
            diff.content_candidates = [
                {"text": r["raw_text"], "source": r["source"], "side": o["side"]}
                for o in observations
                for r in o["readings"]
            ]
