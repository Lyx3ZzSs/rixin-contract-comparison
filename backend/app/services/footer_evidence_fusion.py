"""Give one footer ink object one business event without discarding its sources."""

from app.models import BBox, DiffItem


UNVERIFIED = "VISUAL_FOOTER_CONTENT_UNVERIFIED"


def _same_ink(text: BBox, visual: BBox) -> bool:
    tw, th = text.x1 - text.x0, text.y1 - text.y0
    vw, vh = visual.x1 - visual.x0, visual.y1 - visual.y0
    if min(tw, th, vw, vh) <= 0:
        return False
    intersection = max(0, min(text.x1, visual.x1) - max(text.x0, visual.x0)) * max(
        0, min(text.y1, visual.y1) - max(text.y0, visual.y0)
    )
    # A small residual component entirely inside a localized text span adds no
    # independent spatial claim, even if only a few pen strokes were detected.
    if intersection / (vw * vh) >= 0.95:
        return True
    # Containment alone is insufficient: a broad visual region may include
    # another annotation. Require comparable, local extents as well.
    return (
        intersection / min(tw * th, vw * vh) >= 0.90
        and max(tw, vw) / min(tw, vw) <= 3
        and max(th, vh) / min(th, vh) <= 3
        and visual.x0 >= text.x0 - 18
        and visual.x1 <= text.x1 + 18
        and visual.y0 >= text.y0 - 18
        and visual.y1 <= text.y1 + 18
    )


def fuse_footer_evidence(diffs: list[DiffItem]) -> list[DiffItem]:
    # Operate on copies so callers can still audit the unmodified detections.
    result = [d.model_copy(deep=True) for d in diffs]
    texts = [
        d
        for d in result
        if d.source_type in {"header_footer", "clause"}
        and d.diff_type in {"ADD", "DELETE", "MODIFY"}
        and "VISUAL_FOOTER_ANNOTATION" not in d.review_flags
        # Length is checked on each localized evidence below. A repeated footer
        # may have a long document-wide aggregate while each physical label is short.
    ]
    consumed = set()
    for ink in result:
        if "VISUAL_FOOTER_ANNOTATION" not in ink.review_flags or ink.content_recognition_reason != UNVERIFIED:
            continue
        if ink.diff_type not in {"ADD", "DELETE"}:
            continue
        side = "compare" if ink.diff_type == "ADD" else "original"
        field = f"{side}_evidence"
        remaining = []
        for evidence in getattr(ink, field):
            owners = [
                text
                for text in texts
                if text.diff_type in {ink.diff_type, "MODIFY"}
                and any(
                    e.method in {"header_footer", "char_exact"}
                    and e.page_no == evidence.page_no
                    and (e.highlight_type or text.diff_type) == ink.diff_type
                    and 0
                    < len("".join((e.text or (text.compare_text if side == "compare" else text.original_text)).split()))
                    <= 32
                    and _same_ink(e.bbox, evidence.bbox)
                    for e in getattr(text, field)
                )
            ]
            if evidence.method != "footer_visual_registered" or len(owners) != 1:
                remaining.append(evidence)
                continue
            owner = owners[0]
            # Auxiliary evidence is retained outside the primary highlight list.
            # It cannot promote OCR confidence or become another ADD/DELETE.
            proof = {
                "side": side,
                "source_diff_id": ink.diff_id,
                "diff_type": ink.diff_type,
                "text": ink.compare_text or ink.original_text,
                "content_recognition_status": ink.content_recognition_status,
                "content_recognition_reason": ink.content_recognition_reason,
                "quality_status": ink.quality_status,
                "review_flags": list(ink.review_flags),
                "evidence": evidence.model_dump(mode="json"),
            }
            proofs = owner.match_score_details.setdefault("footer_visual_evidence", [])
            if proof not in proofs:
                proofs.append(proof)
        if not remaining and getattr(ink, field):
            consumed.add(id(ink))
        else:
            setattr(ink, field, remaining)
    return [d for d in result if id(d) not in consumed]
