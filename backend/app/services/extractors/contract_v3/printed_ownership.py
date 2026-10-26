"""Separate printed OCR evidence from overlapping visual seals."""

from copy import deepcopy
import re

from app.services.extractors.contract_v3.artifact import inside, owner, rectangle
from app.services.extractors.contract_v3.geometry import overlap, area, union
from app.services.table_compare.constants import TABLE_HEADERS

HEADERS = TABLE_HEADERS | {"考核项目", "考核标准", "考核内容", "验收标准"}


def printed_text_owner(box, text, score, regions, visual):
    if not visual or visual["raw_layout_type"] not in {"seal", "stamp"}:
        return visual
    width, height = box[2] - box[0], box[3] - box[1]
    seal_width = visual["bbox"][2] - visual["bbox"][0]
    # A long, high-confidence baseline extending well beyond a stamp is
    # printed prose. A company name contained in the stamp cannot qualify.
    if (
        float(score or 0) >= 0.97
        and len(re.sub(r"\s+", "", text)) >= 20
        and width > max(height * 6, seal_width * 1.8)
        and overlap(box, visual["bbox"]) / area(box) < 0.5
    ):
        return owner(box, [r for r in regions if r["raw_layout_type"] not in {"seal", "stamp"}])
    return visual


def recover_table_headers(artifact):
    """Recover verbatim header spans, corroborated by two neighboring headers.

    Never copy a counterpart's wording. Preserve other stamp characters and
    original OCR provenance; only the observed printed substring changes owner.
    """
    additions = []
    for table in artifact["regions"]:
        if table["raw_layout_type"] != "table":
            continue
        tb = table["bbox"]
        headers = [
            b
            for b in artifact["blocks"]
            if b.get("region_id") == table["region_id"]
            and re.sub(r"\s+", "", b["text"]) in HEADERS
            and float(b.get("raw_ocr_score") or b.get("confidence") or 0) >= 0.85
            and b["bbox"][1] < tb[1] + (tb[3] - tb[1]) * 0.2
        ]
        if len(headers) < 2:
            continue
        for block in artifact["blocks"]:
            if block.get("raw_layout_type") not in {"seal", "stamp"} or block.get("source") != "PP-OCRv6":
                continue
            if float(block.get("raw_ocr_score") or 0) < 0.8:
                continue
            for term in sorted(HEADERS, key=len, reverse=True):
                start = block["text"].find(term)
                if start < 0:
                    continue
                chars = [
                    c for c in block.get("ocr_char_provenance", []) if start <= c["text_index"] < start + len(term)
                ]
                if len(chars) != len(term) or "".join(c["char"] for c in chars) != term:
                    continue
                box = union([c["bbox"] for c in chars])
                cy = (box[1] + box[3]) / 2
                if not all(
                    inside([(c["bbox"][0] + c["bbox"][2]) / 2, (c["bbox"][1] + c["bbox"][3]) / 2], table["polygon"])
                    for c in chars
                ):
                    continue
                if (
                    sum(
                        abs((b["bbox"][1] + b["bbox"][3]) / 2 - cy) <= max(box[3] - box[1], b["bbox"][3] - b["bbox"][1])
                        for b in headers
                    )
                    < 2
                ):
                    continue
                new = deepcopy(block)
                new.update(
                    block_id=block["block_id"] + ":printed-header",
                    text=term,
                    bbox=box,
                    polygon=rectangle(box),
                    region_id=table["region_id"],
                    raw_layout_type="table",
                    resolved_block_type="table",
                    reason="observed_header_span_with_neighboring_table_headers",
                    ocr_char_provenance=deepcopy(chars),
                    evidence_missing_text_indices=[],
                )
                for i, c in enumerate(new["ocr_char_provenance"]):
                    c["source_text_index"] = c["text_index"]
                    c["text_index"] = i
                remaining = [i for i in range(len(block["text"])) if not start <= i < start + len(term)]
                indexes = {old: new for new, old in enumerate(remaining)}
                block["text"] = "".join(block["text"][i] for i in remaining)
                block["ocr_char_provenance"] = [
                    c for c in block.get("ocr_char_provenance", []) if c["text_index"] in indexes
                ]
                for c in block["ocr_char_provenance"]:
                    c["source_text_index"] = c["text_index"]
                    c["text_index"] = indexes[c["text_index"]]
                block["evidence_missing_text_indices"] = [
                    indexes[i] for i in block.get("evidence_missing_text_indices", []) if i in indexes
                ]
                additions.append(new)
                break
    artifact["blocks"].extend(additions)
