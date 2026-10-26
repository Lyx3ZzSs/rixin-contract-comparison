"""Lossless V3 provenance and native canonical ownership, separate from business roles."""

from collections import Counter
from itertools import groupby
import copy
import math
import unicodedata

from app.services.extractors.contract_v3.geometry import TITLE_FAMILY, area, overlap, union, is_suspicious_char


def transform_point(point, matrix):
    x, y = point
    a, b, c, d, e, f = matrix
    return [a * x + c * y + e, b * x + d * y + f]


def enclosing(polygon):
    return [
        min(p[0] for p in polygon),
        min(p[1] for p in polygon),
        max(p[0] for p in polygon),
        max(p[1] for p in polygon),
    ]


def rectangle(box):
    x0, y0, x1, y1 = box
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def regions_from_official(sample, boxes, network):
    result = []
    matrix = sample["coordinate_transform"]["pixel_to_native"]
    for index, box in enumerate(boxes):
        raw_poly = box.get("polygon_points")
        polygon = [transform_point(p, matrix) for p in (raw_poly or rectangle(box["coordinate"]))]
        matches = [
            row for row in network["boxes"] if int(row[0]) == box["cls_id"] and abs(row[1] - box["score"]) < 1e-7
        ]
        matched = (
            min(matches, key=lambda row: sum(abs(round(v) - w) for v, w in zip(row[2:6], box["coordinate"])))
            if matches
            else None
        )
        result.append(
            dict(
                region_id=f"{sample['id']}:v3:{index}",
                raw_layout_type=box["label"],
                raw_score=box["score"],
                raw_bbox=copy.deepcopy(box["coordinate"]),
                raw_polygon=copy.deepcopy(raw_poly),
                raw_model_order=box.get("order"),
                raw_network_order=matched[6:] if matched else None,
                raw_full_model_sequence=index,
                official_postprocess_result=copy.deepcopy(box),
                polygon=polygon,
                bbox=enclosing(polygon),
                polygon_source="v3_polygon" if raw_poly else "bbox_envelope",
                coordinate_transform=copy.deepcopy(sample["coordinate_transform"]),
            )
        )
    return result


def inside(point, polygon):
    x, y = point
    hit = False
    for p, q in zip(polygon, polygon[1:] + polygon[:1]):
        if (p[1] > y) != (q[1] > y) and x < (q[0] - p[0]) * (y - p[1]) / (q[1] - p[1]) + p[0]:
            hit = not hit
    return hit


def owner(box, regions):
    center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
    candidates = [r for r in regions if inside(center, r["polygon"])]
    if not candidates:
        return None
    # Nested visual regions: prefer precise semantic area; deterministic tie break.
    return min(candidates, key=lambda r: (area(r["bbox"]), -r["raw_score"], r["region_id"]))


def assign_order(blocks, regions):
    lookup = {r["region_id"]: r for r in regions}
    anchors = sorted(
        [r for r in regions if r.get("raw_full_model_sequence") is not None], key=lambda r: r["raw_full_model_sequence"]
    )
    fallback = []
    ranked = []
    for block in blocks:
        region = lookup.get(block.get("region_id"))
        if region and region.get("raw_full_model_sequence") is not None:
            rank = float(region["raw_full_model_sequence"])
            block["order_source"] = "v3_model"
        else:
            # Insert relative to the nearest overlapping column, not an unconditional tail bucket.
            candidates = [
                r for r in anchors if min(r["bbox"][2], block["bbox"][2]) > max(r["bbox"][0], block["bbox"][0])
            ]
            candidates = candidates or anchors
            if candidates:
                cy = (block["bbox"][1] + block["bbox"][3]) / 2
                near = min(candidates, key=lambda r: abs((r["bbox"][1] + r["bbox"][3]) / 2 - cy))
                after = cy >= (near["bbox"][1] + near["bbox"][3]) / 2
                rank = near["raw_full_model_sequence"] + (0.5 if after else -0.5)
            else:
                rank = block["bbox"][1]
            block["order_source"] = "geometry_fallback"
            fallback.append(block["block_id"])
        ranked.append((rank, block["bbox"][1], block["bbox"][0], block["block_id"], block))
    ranked.sort(key=lambda row: row[:4])
    blocks[:] = [row[4] for row in ranked]
    for index, block in enumerate(blocks, 1):
        block["reading_order"] = index
    return fallback


def merge_native(sample, raw, regions):
    chars, blocks, lines = [], [], []
    for bi, native in enumerate(raw["blocks"]):
        if native["type"] != 0:
            continue
        for li, line in enumerate(native["lines"]):
            ids = []
            for si, span in enumerate(line["spans"]):
                for ci, char in enumerate(span["chars"]):
                    for value in char["c"]:
                        idx = len(chars)
                        region = owner(char["bbox"], regions)
                        good = (
                            char.get("native_visible", True)
                            and (value.isprintable() or value.isspace())
                            and (value.isspace() or not is_suspicious_char(value))
                            and all(math.isfinite(v) for v in char["bbox"])
                            and area(char["bbox"]) > 1e-8
                        )
                        chars.append(
                            dict(
                                index=idx,
                                char=value,
                                bbox=list(char["bbox"]),
                                native_location=[bi, li, si, ci],
                                font=span["font"],
                                size=span["size"],
                                flags=span["flags"],
                                usable=good,
                                native_visible=char.get("native_visible", True),
                                region_id=region["region_id"] if region else None,
                                assignment_reason="polygon_center" if region else "unassigned",
                            )
                        )
                        ids.append(idx)
            assigned = {chars[i]["region_id"] for i in ids if chars[i]["region_id"] is not None}
            if len(assigned) == 1:
                rid = next(iter(assigned))
                region = next(r for r in regions if r["region_id"] == rid)
                for idx in ids:
                    char = chars[idx]
                    if (
                        char["region_id"] is None
                        and (char["char"].isspace() or unicodedata.category(char["char"]).startswith("P"))
                        and overlap(char["bbox"], region["bbox"]) > 0
                    ):
                        char.update(region_id=rid, assignment_reason="same_native_line_boundary_punctuation")
            lines.append(dict(line_id=f"{bi}:{li}", bbox=line["bbox"], char_indices=ids))
            for (region_id, usable), group in groupby(ids, key=lambda i: (chars[i]["region_id"], chars[i]["usable"])):
                indices = list(group)
                if not usable:
                    continue
                region = next((r for r in regions if r["region_id"] == region_id), None)
                box = union([chars[i]["bbox"] for i in indices])
                blocks.append(
                    dict(
                        block_id=f"{sample['id']}:native:{len(blocks)}",
                        line_id=f"{bi}:{li}",
                        region_id=region_id,
                        text="".join(chars[i]["char"] for i in indices),
                        bbox=box,
                        polygon=copy.deepcopy(region["polygon"]) if region else rectangle(box),
                        raw_layout_type=region["raw_layout_type"] if region else None,
                        resolved_block_type=region["raw_layout_type"] if region else "text",
                        confidence=region["raw_score"] if region else 0.0,
                        reason="v3_visual_semantics" if region else "native_unassigned",
                        source="native_pdf",
                        native_char_indices=indices,
                    )
                )
    blocks = join_visual_native_lines(blocks, chars)
    fallback = assign_order(blocks, regions)
    artifact = dict(
        schema_version=1,
        layout_model="PP-DocLayoutV3",
        page_id=sample["id"],
        page_no=sample["page"],
        pdf=sample["pdf"],
        pdf_sha256=sample["pdf_sha256"],
        width=raw["width"],
        height=raw["height"],
        coordinate_transform=sample["coordinate_transform"],
        regions=copy.deepcopy(regions),
        native_chars=chars,
        native_lines=lines,
        blocks=blocks,
        order_fallback_blocks=fallback,
        errors=[],
    )
    from app.services.extractors.contract_v3.text_contract_repair import join_native_field_fragments

    join_native_field_fragments(artifact)
    verify_conservation(artifact)
    return artifact


def join_visual_native_lines(blocks, chars):
    """Rejoin native fragments on the same baseline before region reading order.

    Preserve every source fragment and its V3 association; no new title is inferred.
    Tables, columns with a gap, and competing visual types remain separate.
    """
    groups = []
    for b in sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0])):
        group = None
        for prior in reversed(groups):
            p = prior[0]
            labels = {x["raw_layout_type"] for x in [*prior, b]} - {None}
            compatible = labels <= {"text"} or (
                labels <= TITLE_FAMILY and None not in {x["raw_layout_type"] for x in [*prior, b]}
            )
            heights = [x["bbox"][3] - x["bbox"][1] for x in [p, b]]
            baseline = abs((p["bbox"][1] + p["bbox"][3] - b["bbox"][1] - b["bbox"][3]) / 2) <= min(heights) * 0.3
            ordered = sorted([*prior, b], key=lambda x: x["bbox"][0])
            same_region = bool(b["region_id"]) and all(x["region_id"] == b["region_id"] for x in prior)
            # Native tab stops separate a marker from its body within one V3 region.
            gap_limit = min(heights) * (2 if same_region else 0.8)
            contiguous = all(-0.5 <= y["bbox"][0] - x["bbox"][2] <= gap_limit for x, y in zip(ordered, ordered[1:]))
            if compatible and baseline and contiguous:
                group = prior
                break
        if group is None:
            groups.append([b])
        else:
            group.append(b)
    result = []
    for group in groups:
        if len(group) == 1:
            result.append(group[0])
            continue
        group.sort(key=lambda b: b["bbox"][0])
        representative = next((b for b in group if b["region_id"]), group[0])
        b = copy.deepcopy(representative)
        b["native_fragments"] = copy.deepcopy(group)
        b["native_char_indices"] = [i for item in group for i in item["native_char_indices"]]
        b["text"] = "".join(chars[i]["char"] for i in b["native_char_indices"])
        b["bbox"] = union([item["bbox"] for item in group])
        b["geometry_merge_reason"] = "same_native_visual_baseline_contiguous_fragments"
        result.append(b)
    return result


def verify_conservation(artifact):
    expected = [c["index"] for c in artifact["native_chars"] if c["usable"]]
    used = [i for b in artifact["blocks"] for i in b.get("native_char_indices", [])]
    assert Counter(expected) == Counter(used), "Native canonical ownership must be exactly once"
    assert all(
        b["text"] == "".join(artifact["native_chars"][i]["char"] for i in b["native_char_indices"])
        for b in artifact["blocks"]
        if b["source"] == "native_pdf"
    )
    artifact["native_conservation"] = dict(
        pass_=True,
        raw_chars=len(artifact["native_chars"]),
        canonical_chars=len(used),
        unusable_chars=len(artifact["native_chars"]) - len(used),
    )


def to_page(artifact):
    from app.models import BBox, CharBox, Page, TextBlock
    from app.services.layout_analysis import flow_role_for_region

    def bb(values):
        return BBox(**dict(zip(("x0", "y0", "x1", "y1"), values)))

    regions = {r["region_id"]: r for r in artifact["regions"]}
    blocks = []
    for b in artifact["blocks"]:
        region = regions.get(b.get("region_id"))
        blocks.append(
            TextBlock(
                block_id=b["block_id"],
                page_no=artifact["page_no"],
                text=b["text"],
                bbox=bb(b["bbox"]),
                block_type=b["resolved_block_type"],
                block_role=b["resolved_block_type"],
                source=b["source"],
                confidence=1.0 if b["source"] == "native_pdf" else b.get("raw_ocr_score", b.get("confidence")),
                reading_order=b["reading_order"],
                raw_html=b.get("raw_html", ""),
                table_cell_bboxes=b.get("table_cell_bboxes", []),
                table_cells=b.get("table_cells", []),
                table_model_diagnostics=b.get("table_model_diagnostics", {}),
                structure_status=b.get("structure_status", "UNKNOWN"),
                table_validation=b.get("table_validation", {}),
                table_id=b.get("table_id", ""),
                layout_block_id=b.get("region_id") or "",
                layout_order=b["reading_order"],
                layout_bbox=bb(region["bbox"]) if region else None,
                flow_role=flow_role_for_region(b["resolved_block_type"]),
                enter_clause_compare=b.get("enter_clause_compare", True),
                semantic_reasons=[b["reason"]],
                char_boxes=[
                    CharBox(
                        char=artifact["native_chars"][i]["char"],
                        page_no=artifact["page_no"],
                        bbox=bb(artifact["native_chars"][i]["bbox"]),
                        text_index=j,
                        confidence=1.0,
                    )
                    for j, i in enumerate(b.get("native_char_indices", []))
                ]
                + [
                    CharBox(char=c["char"], page_no=artifact["page_no"], bbox=bb(c["bbox"]), text_index=c["text_index"],
                            confidence=c.get("confidence", b.get("raw_ocr_score", b.get("confidence"))))
                    for c in b.get("ocr_char_provenance", [])
                ],
            )
        )
    return Page(page_no=artifact["page_no"], width=artifact["width"], height=artifact["height"], blocks=blocks)


class UnifiedArtifactAdapter:
    """Translate the unified page artifact to the existing Page contract."""

    def to_page(self, artifact):
        page = to_page(artifact)
        # Publish visual-only objects at the final adapter boundary. Internal
        # semantic repair deliberately keeps a 1:1 mapping to text artifacts.
        from app.models import BBox, TextBlock

        represented = {b.layout_block_id for b in page.blocks if b.block_type in {"seal", "stamp"}}
        for region in artifact["regions"]:
            if region["raw_layout_type"] not in {"seal", "stamp"} or region["region_id"] in represented:
                continue
            bbox = BBox(**dict(zip(("x0", "y0", "x1", "y1"), region["bbox"])))
            page.blocks.append(TextBlock(
                block_id=f"{region['region_id']}:visual", page_no=page.page_no,
                text="", bbox=bbox, layout_bbox=bbox, layout_block_id=region["region_id"],
                block_type="seal", block_role="seal", source="PP-DocLayoutV3",
                confidence=region["raw_score"], layout_order=region.get("raw_model_order"),
                enter_clause_compare=False, semantic_reasons=["layout_seal_without_text"],
            ))
        return page
