"""Reuse production text/margin repair and correct conservative geometry ownership.

This module never calls recognition and never changes V3 raw fields or raw OCR.
"""

import copy
import re
from pathlib import Path
from app.services.extractors.contract_v3.geometry import area, overlap, union
from app.services.extractors.contract_v3.artifact import rectangle, to_page, verify_conservation


def map_verified_repair(block, corrected):
    """Map an already approved repair to immutable OCR source units.

    This function does not decide whether a glyph is a permille sign. The
    existing verifier owns that decision. Unsupported edits fail closed.
    """
    raw = block["text"]
    chars = {c["text_index"]: c for c in block["ocr_char_provenance"]}
    mapped, reverse = [], {}
    source_index = 0
    for canonical_index, ch in enumerate(corrected):
        size = 1
        if raw[source_index : source_index + 1] != ch:
            if ch != "‰" or raw[source_index : source_index + 1] != "%":
                raise ValueError("Unsupported verified text repair")
            if len(raw) - source_index > len(corrected) - canonical_index and raw[
                source_index + 1 : source_index + 2
            ] in ("o", "O", "0", "。"):
                size = 2
        refs = []
        for i in range(source_index, source_index + size):
            if i not in chars:
                if raw[i].isspace() and size == 1:
                    continue
                raise ValueError("Verified repair has no OCR source geometry")
            source = copy.deepcopy(chars[i])
            source.update(
                source_text_index=i, source_char_id=f"{block['block_id']}:source:{i}", source_precision="word"
            )
            refs.append(source)
            reverse[source["source_char_id"]] = [canonical_index]
        if refs:
            c = copy.deepcopy(refs[0])
            c.update(
                char=ch,
                text_index=canonical_index,
                source_refs=refs,
                source_range=[source_index, source_index + size],
                source_precision="word",
            )
            if size == 2:
                c.update(
                    provenance_kind="NORMALIZED_MANY_TO_ONE",
                    repair_reason="OCR_PERMILLE_NORMALIZATION",
                    source_polygons=[s["polygon"] for s in refs],
                    bbox=union([s["bbox"] for s in refs]),
                )
                # Envelope is derived, not a synthetic glyph box. The exact
                # source polygons remain the evidence geometry below.
                c["polygon"] = rectangle(c["bbox"])
                c["evidence_geometry"] = dict(kind="SOURCE_POLYGON_UNION", polygons=c["source_polygons"])
            if raw[source_index : source_index + size] != ch:
                c.update(
                    raw_ocr_char=raw[source_index : source_index + size],
                    canonical_repair="production_visual_per_mille_glyph_verification",
                )
            mapped.append(c)
        source_index += size
    if source_index != len(raw):
        raise ValueError("Verified repair did not consume all source characters")
    block.update(ocr_char_provenance=mapped, source_to_canonical=reverse)


def join_native_field_fragments(artifact):
    blocks = artifact["blocks"]
    removed = set()
    for b in blocks:
        if (
            b["source"] != "native_pdf"
            or not b["text"].rstrip().endswith(("：", ":"))
            or b["raw_layout_type"] not in {None, "text"}
        ):
            continue
        h = b["bbox"][3] - b["bbox"][1]
        siblings = [
            c
            for c in blocks
            if c is not b
            and c["source"] == "native_pdf"
            and c["bbox"][0] >= b["bbox"][2]
            and abs((c["bbox"][1] + c["bbox"][3] - b["bbox"][1] - b["bbox"][3]) / 2) < h * 0.3
        ]
        if not siblings or any(
            c["raw_layout_type"] not in {None, "text"} or not re.fullmatch(r"[\s/。．._—－-]+", c["text"])
            for c in siblings
        ):
            continue
        # Blank-field placeholders need their label to survive downstream noise
        # filtering. Never bridge to another textual column or into a table.
        group = [b, *sorted(siblings, key=lambda c: c["bbox"][0])]
        originals = copy.deepcopy(group)
        b["native_fragments"] = originals
        b["native_char_indices"] = [i for c in originals for i in c["native_char_indices"]]
        b["text"] = "".join(artifact["native_chars"][i]["char"] for i in b["native_char_indices"])
        b["bbox"] = union([c["bbox"] for c in originals])
        b["polygon"] = rectangle(b["bbox"])
        b["geometry_merge_reason"] = "same_baseline_label_and_punctuation_only_field_fragments"
        removed.update(c["block_id"] for c in siblings)
    artifact["blocks"] = [b for b in blocks if b["block_id"] not in removed]
    verify_conservation(artifact)


def apply_text_contract_repairs(artifacts):
    from app.models import Document
    from app.services.header_footer_compare import HeaderFooterComparator
    from app.services.extractors.ppocrv5 import PPOCRV5Extractor

    doc = Document(
        filename=Path(artifacts[0]["pdf"]).name,
        path=artifacts[0]["pdf"],
        page_count=len(artifacts),
        pages=[to_page(a) for a in artifacts],
    )
    footer = HeaderFooterComparator().owned_footer_blocks(doc)
    extractor = PPOCRV5Extractor()
    # Existing verified native/glyph repair performs no recognition request.
    extractor._correct_verified_per_mille_ocr(doc.pages, Path(artifacts[0]["pdf"]))
    repairs = []

    def compact(t):
        return re.sub(r"[\s:：]", "", t)

    for a, page in zip(artifacts, doc.pages, strict=True):
        for b, p in zip(a["blocks"], page.blocks, strict=True):
            if b["source"] == "PP-OCRv6":
                b.setdefault("raw_ocr_text", b["text"])
            if (a["page_no"], b["block_id"]) in footer and b["resolved_block_type"] == "text":
                b.update(
                    resolved_block_type="footer", reason="HeaderFooterComparator.document_repeated_footer_evidence"
                )
                b["margin_repair"] = dict(
                    error_class="LAYOUT_RECALL_ERROR", accepted=True, source="production_footer_evidence"
                )
                repairs.append(dict(block_id=b["block_id"], type="footer_ownership"))
            if b["source"] == "PP-OCRv6" and b["text"] != p.text:
                map_verified_repair(b, p.text)
                a["errors"] = [
                    e
                    for e in a["errors"]
                    if not (
                        e.get("type") == "TEXT_REPAIR_REVIEW_REQUIRED"
                        and e.get("block_id") == b["block_id"]
                        and e.get("reason") == "length_changing_repair_requires_index_map"
                    )
                ]
                b.update(text=p.text, canonical_text_repair="PPOCRV5Extractor.verified_per_mille")
                repairs.append(
                    dict(block_id=b["block_id"], type="verified_per_mille", raw_text=b["raw_ocr_text"], text=b["text"])
                )
            if b["source"] != "PP-OCRv6" or b["raw_layout_type"] not in {"seal", "stamp"}:
                continue
            region = next(r for r in a["regions"] if r["region_id"] == b["region_id"])
            outside = 1 - overlap(b["bbox"], region["bbox"]) / area(b["bbox"])
            height = b["bbox"][3] - b["bbox"][1]
            # A crossing horizontal line with an independently visible, same-row
            # counterpart cannot inherit a seal role just because its center
            # overlaps the seal. Content of either field is not hard-coded.
            peers = [
                x
                for x in a["blocks"]
                if x["raw_layout_type"] == "text"
                and len(compact(b["text"])) >= 4
                and compact(x["text"]) == compact(b["text"])
                and abs((x["bbox"][1] + x["bbox"][3] - b["bbox"][1] - b["bbox"][3]) / 2) < height
            ]
            if peers and outside > 0.15 and (b["bbox"][2] - b["bbox"][0]) / height > 4:
                b.update(resolved_block_type="text", reason="geometry.crossing_seal_line+same_row_text_counterpart")
                b["geometry_assignment_repair"] = dict(
                    error_class="GEOMETRY_ASSIGNMENT_ERROR",
                    outside_seal_fraction=outside,
                    corroborating_blocks=[x["block_id"] for x in peers],
                    original_region_id=b["region_id"],
                )
                repairs.append(dict(block_id=b["block_id"], type="seal_crossing_text_ownership"))
    return repairs
