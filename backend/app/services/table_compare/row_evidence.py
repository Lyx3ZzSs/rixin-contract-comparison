"""Resolve named table fields against located source lines, independent of grids."""

from app.services.table_compare import utils


def metadata_row_present(text, blocks, page_no):
    labels = utils.table_metadata_labels(text)
    if not labels:
        return False
    target = utils.normalize_cell_for_compare(text)
    candidates = [
        b for b in blocks if b.page_no == page_no and b.bbox and b.text.strip() and "<table" not in b.text.lower()
    ]
    anchors = [
        b
        for b in candidates
        if utils.table_metadata_labels(b.text) and utils.table_metadata_labels(b.text)[0] == labels[0]
    ]
    # Repeated fields on a page cannot establish unique row identity.
    if len(anchors) != 1:
        return False
    anchor = anchors[0]
    box = anchor.bbox
    height = box.y1 - box.y0
    if height <= 0:
        return False
    band = [
        b
        for b in candidates
        if abs((b.bbox.y0 + b.bbox.y1 - box.y0 - box.y1) / 2) <= height * 0.7 and b.bbox.y1 - b.bbox.y0 <= height * 2
    ]
    source = "".join(b.text for b in sorted(band, key=lambda b: b.bbox.x0))
    return utils.normalize_cell_for_compare(source) == target


def evidence_text_confidence(evidences, blocks):
    """Carry only readings supported by both their text and local source box."""
    scores = []
    for evidence in evidences:
        text = utils.normalize_cell_for_compare(evidence.text)
        if not text:
            continue
        for block in blocks:
            if block.page_no != evidence.page_no or not block.bbox or "<table" in block.text.lower():
                continue
            source = utils.normalize_cell_for_compare(block.text)
            if not source or not (source in text or text in source):
                continue
            a, b = evidence.bbox, block.bbox
            if min(a.x1, b.x1) > max(a.x0, b.x0) and min(a.y1, b.y1) > max(a.y0, b.y0):
                scores.append(block.confidence)
    return min(scores) if scores else None
