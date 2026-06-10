from __future__ import annotations

from app.models import BBox, Page, TextBlock


def assign_page_reading_order(page: Page) -> None:
    """Assign deterministic reading order, using V3 flow roles when available."""
    blocks = [block for block in page.blocks if _valid_bbox(block.bbox)]
    if not blocks:
        return
    ordered = _order_flow_blocks(page, blocks) if any(block.flow_role for block in blocks) else _order_v2_blocks(page, blocks)
    for index, block in enumerate(ordered, start=1):
        block.reading_order = index


def reading_order_conflict_count(page: Page) -> int:
    ordered = sorted(
        (block for block in page.blocks if block.reading_order is not None and block.layout_order is not None),
        key=lambda block: block.reading_order or 0,
    )
    return sum(
        (left.layout_order or 0) > (right.layout_order or 0)
        for left, right in zip(ordered, ordered[1:], strict=False)
    )


def _order_flow_blocks(page: Page, blocks: list[TextBlock]) -> list[TextBlock]:
    headers = sorted(
        (block for block in blocks if block.flow_role == "margin" and _mid_y(block.bbox) < page.height / 2),
        key=_geometry_key,
    )
    footers = sorted(
        (block for block in blocks if block.flow_role == "margin" and _mid_y(block.bbox) >= page.height / 2),
        key=_geometry_key,
    )
    excluded_ids = {id(block) for block in [*headers, *footers]}
    flow = [
        block
        for block in blocks
        if id(block) not in excluded_ids and block.flow_role != "noise"
    ]
    trailing = sorted(
        (block for block in blocks if block.flow_role == "noise"),
        key=_geometry_key,
    )

    anchors = sorted(
        (
            block
            for block in flow
            if page.width > 0
            and (block.bbox.x1 - block.bbox.x0) / page.width >= 0.65
            and block.flow_role != "aside"
        ),
        key=_geometry_key,
    )
    anchor_ids = {id(block) for block in anchors}
    remaining = [block for block in flow if id(block) not in anchor_ids]

    ordered: list[TextBlock] = []
    consumed: set[int] = set()
    previous_y = 0.0
    for anchor in anchors:
        anchor_mid = _mid_y(anchor.bbox)
        section = [
            block
            for block in remaining
            if id(block) not in consumed and previous_y <= _mid_y(block.bbox) < anchor_mid
        ]
        ordered.extend(_order_flow_section(section))
        consumed.update(id(block) for block in section)
        ordered.append(anchor)
        previous_y = anchor_mid
    ordered.extend(_order_flow_section([block for block in remaining if id(block) not in consumed]))
    return [*headers, *_group_captions_with_targets(ordered), *trailing, *footers]


def _order_flow_section(blocks: list[TextBlock]) -> list[TextBlock]:
    asides = [block for block in blocks if block.flow_role == "aside"]
    main = _order_columns([block for block in blocks if block.flow_role != "aside"])
    for aside in sorted(asides, key=_geometry_key):
        if not main:
            main.append(aside)
            continue
        target_index = min(range(len(main)), key=lambda index: abs(_mid_y(main[index].bbox) - _mid_y(aside.bbox)))
        main.insert(target_index + 1, aside)
    return main


def _group_captions_with_targets(blocks: list[TextBlock]) -> list[TextBlock]:
    result = list(blocks)
    for caption in [block for block in blocks if block.flow_role == "caption"]:
        targets = [
            block
            for block in blocks
            if block.flow_role in {"table", "non_text"}
            and _horizontal_coverage(caption.bbox, block.bbox) >= 0.3
        ]
        if not targets:
            continue
        target = min(targets, key=lambda block: abs(_mid_y(block.bbox) - _mid_y(caption.bbox)))
        caption_index = result.index(caption)
        target_index = result.index(target)
        if caption.bbox.y0 <= target.bbox.y0 and caption_index > target_index:
            result.pop(caption_index)
            result.insert(result.index(target), caption)
    return result


def _order_v2_blocks(page: Page, blocks: list[TextBlock]) -> list[TextBlock]:
    spanning = [
        block
        for block in blocks
        if page.width > 0 and (block.bbox.x1 - block.bbox.x0) / page.width >= 0.65
    ]
    spanning_ids = {id(block) for block in spanning}
    remaining = [block for block in blocks if id(block) not in spanning_ids]
    anchors = sorted(spanning, key=_geometry_key)

    ordered: list[TextBlock] = []
    consumed: set[int] = set()
    previous_y = 0.0
    for anchor in anchors:
        anchor_mid = _mid_y(anchor.bbox)
        section = [
            block
            for block in remaining
            if id(block) not in consumed and previous_y <= _mid_y(block.bbox) < anchor_mid
        ]
        ordered.extend(_order_columns(section))
        consumed.update(id(block) for block in section)
        ordered.append(anchor)
        previous_y = anchor_mid
    ordered.extend(_order_columns([block for block in remaining if id(block) not in consumed]))
    return ordered


def _order_columns(blocks: list[TextBlock]) -> list[TextBlock]:
    if len(blocks) <= 1:
        return blocks
    columns: list[list[TextBlock]] = []
    for block in sorted(blocks, key=lambda item: (item.bbox.x0, item.bbox.y0, item.block_id)):
        candidates = [
            column
            for column in columns
            if _horizontal_coverage(block.bbox, _column_bbox(column)) >= 0.35
        ]
        if candidates:
            best = max(candidates, key=lambda column: _horizontal_coverage(block.bbox, _column_bbox(column)))
            best.append(block)
        else:
            columns.append([block])
    columns.sort(key=lambda column: (_column_bbox(column).x0, _column_bbox(column).y0))
    return [block for column in columns for block in sorted(column, key=_geometry_key)]


def _column_bbox(blocks: list[TextBlock]) -> BBox:
    return BBox(
        x0=min(block.bbox.x0 for block in blocks),
        y0=min(block.bbox.y0 for block in blocks),
        x1=max(block.bbox.x1 for block in blocks),
        y1=max(block.bbox.y1 for block in blocks),
    )


def _horizontal_coverage(left: BBox, right: BBox) -> float:
    overlap = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    smaller = min(max(0.0, left.x1 - left.x0), max(0.0, right.x1 - right.x0))
    return overlap / smaller if smaller > 0 else 0.0


def _geometry_key(block: TextBlock) -> tuple[float, float, int, str]:
    return block.bbox.y0, block.bbox.x0, block.layout_order or 0, block.block_id


def _mid_y(bbox: BBox) -> float:
    return (bbox.y0 + bbox.y1) / 2


def _valid_bbox(bbox: BBox) -> bool:
    return bbox.x1 > bbox.x0 and bbox.y1 > bbox.y0
