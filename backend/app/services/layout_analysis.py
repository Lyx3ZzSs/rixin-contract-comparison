from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz

from app.models import BBox, Document, LayoutQualityReport, Page, ParseWarningDetail, TextBlock
from app.services.reading_order import assign_page_reading_order


LAYOUT_PARSER_VERSION = "v2"

LABEL_ALIASES = {
    "page_header": "header",
    "page_footer": "footer",
    "vision_footnote": "footnote",
    "stamp": "seal",
    "signature": "seal",
    "paragraph": "text",
    "content": "text",
}

NON_TEXT_REGION_TYPES = {
    "abandon",
    "chart",
    "figure",
    "formula",
    "image",
    "seal",
}


@dataclass
class LayoutRegion:
    region_type: str
    bbox: BBox
    page_number: int
    confidence: float = 1.0
    text: str = ""
    raw_html: str = ""
    table_cell_bboxes: list[list[float]] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)
    original_label: str = ""
    layout_order: int | None = None


@dataclass
class LayoutResult:
    regions: list[LayoutRegion] = field(default_factory=list)
    page_dimensions: dict[int, tuple[float, float]] = field(default_factory=dict)
    page_count: int = 0
    quality: LayoutQualityReport = field(default_factory=LayoutQualityReport)


class PPStructureLayoutAdapter:
    """Convert PP-Structure responses into one validated internal layout model."""

    def __init__(self, mode: str = "v2") -> None:
        self.mode = mode

    def parse(self, payload: dict[str, Any], page_sizes: list[tuple[float, float]]) -> LayoutResult:
        result = payload.get("result")
        if not isinstance(result, dict):
            return LayoutResult(
                page_count=len(page_sizes),
                quality=self._quality(page_count=len(page_sizes), invalid_bbox_count=1),
            )

        page_results = result.get("layoutParsingResults") or result.get("layout_parsing_results") or []
        if not isinstance(page_results, list):
            page_results = []
        remote_sizes = self.remote_pages(result.get("dataInfo") or result.get("data_info"))
        page_count = max(len(page_sizes), len(page_results), 1)

        regions: list[LayoutRegion] = []
        dimensions: dict[int, tuple[float, float]] = {}
        label_counts: Counter[str] = Counter()
        invalid_bbox_count = 0
        empty_region_count = 0
        table_region_count = 0
        table_cell_matched_count = 0
        table_cell_unmatched_count = 0

        for page_index in range(page_count):
            page_no = page_index + 1
            width, height = page_sizes[page_index] if page_index < len(page_sizes) else (595.0, 842.0)
            remote_w, remote_h = remote_sizes[page_index] if page_index < len(remote_sizes) else (width, height)
            dimensions[page_no] = (width, height)
            page_payload = page_results[page_index] if page_index < len(page_results) else {}
            page_regions, page_stats = self._parse_page(
                page_no, width, height, remote_w, remote_h, page_payload
            )
            regions.extend(page_regions)
            label_counts.update(page_stats["labels"])
            invalid_bbox_count += page_stats["invalid_bbox_count"]
            empty_region_count += page_stats["empty_region_count"]
            table_region_count += page_stats["table_region_count"]
            table_cell_matched_count += page_stats["table_cell_matched_count"]
            table_cell_unmatched_count += page_stats["table_cell_unmatched_count"]

        quality = self._quality(
            page_count=page_count,
            region_count=len(regions),
            label_counts=dict(label_counts),
            invalid_bbox_count=invalid_bbox_count,
            empty_region_count=empty_region_count,
            table_region_count=table_region_count,
            table_cell_matched_count=table_cell_matched_count,
            table_cell_unmatched_count=table_cell_unmatched_count,
        )
        return LayoutResult(
            regions=regions,
            page_dimensions=dimensions,
            page_count=page_count,
            quality=quality,
        )

    def to_document(self, layout: LayoutResult, source_path: str | Path) -> Document:
        source_path = Path(source_path)
        pages: list[Page] = []
        for page_no in sorted(layout.page_dimensions):
            width, height = layout.page_dimensions[page_no]
            page_regions = [region for region in layout.regions if region.page_number == page_no]
            blocks = [
                TextBlock(
                    block_id=f"p{page_no}_ppstructure_b{region.layout_order or index + 1}",
                    page_no=page_no,
                    text=region.text,
                    bbox=region.bbox,
                    block_type=region.region_type,
                    confidence=region.confidence,
                    layout_order=region.layout_order,
                    layout_bbox=region.bbox,
                    block_role=region.original_label,
                    flow_role=flow_role_for_region(region.region_type) if self.mode == "v3" else "",
                    source="ppstructure_layout",
                    layout_match_status="structure_only",
                    layout_match_reason="region returned by PP-Structure",
                    raw_html=region.raw_html,
                    table_cell_bboxes=region.table_cell_bboxes,
                )
                for index, region in enumerate(page_regions)
            ]
            page = Page(page_no=page_no, width=width, height=height, blocks=blocks)
            assign_page_reading_order(page)
            pages.append(page)
        layout.quality.reading_order_count = sum(
            block.reading_order is not None for page in pages for block in page.blocks
        )
        return Document(
            filename=source_path.name,
            path=str(source_path),
            page_count=layout.page_count,
            pages=pages,
        )

    def _parse_page(
        self,
        page_no: int,
        width: float,
        height: float,
        remote_w: float,
        remote_h: float,
        page_payload: Any,
    ) -> tuple[list[LayoutRegion], dict[str, Any]]:
        stats: dict[str, Any] = {
            "labels": Counter(),
            "invalid_bbox_count": 0,
            "empty_region_count": 0,
            "table_region_count": 0,
            "table_cell_matched_count": 0,
            "table_cell_unmatched_count": 0,
        }
        if not isinstance(page_payload, dict):
            return [], stats
        pruned = page_payload.get("prunedResult") or page_payload.get("pruned_result") or page_payload
        if not isinstance(pruned, dict):
            return [], stats

        items = pruned.get("parsing_res_list") or pruned.get("parsingResList")
        if not isinstance(items, list):
            return self._parse_overall_ocr(page_no, width, height, remote_w, remote_h, pruned, stats), stats

        candidates: list[tuple[LayoutRegion, BBox]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            original_label = str(item.get("block_label") or item.get("label") or "text").strip().lower()
            stats["labels"][original_label] += 1
            raw_bbox = item.get("block_bbox") or item.get("bbox")
            bbox = self.parse_bbox(raw_bbox, width, height, remote_w, remote_h)
            if bbox is None:
                stats["invalid_bbox_count"] += 1
                if self.mode == "legacy":
                    bbox = BBox(x0=0, y0=0, x1=width, y1=height)
                else:
                    continue
            text = self.clean_text(item.get("block_content") or item.get("content") or item.get("text") or "")
            if not text:
                stats["empty_region_count"] += 1
            region_type = original_label if self.mode == "legacy" else canonical_region_type(original_label)
            if not text and self.mode == "legacy":
                continue
            if not text and region_type not in NON_TEXT_REGION_TYPES and region_type not in {"aside_text", "footer"}:
                continue
            confidence = self._confidence(item)
            candidates.append(
                (
                    LayoutRegion(
                        region_type=region_type,
                        bbox=bbox,
                        page_number=page_no,
                        confidence=confidence,
                        text=text,
                        raw_html=text if region_type == "table" and "<table" in text.lower() else "",
                        raw_data=dict(item),
                        original_label=original_label,
                        layout_order=index + 1,
                    ),
                    bbox,
                )
            )

        table_entries = self._table_entries(pruned, width, height, remote_w, remote_h)
        table_pairs = self._match_tables(candidates, table_entries)
        for candidate_index, entry_index in table_pairs.items():
            region = candidates[candidate_index][0]
            region.table_cell_bboxes = table_entries[entry_index]["cells"]
            stats["table_cell_matched_count"] += 1
        table_count = sum(region.region_type == "table" for region, _ in candidates)
        stats["table_region_count"] = table_count
        stats["table_cell_unmatched_count"] = max(0, table_count - len(table_pairs))

        regions = [region for region, _ in candidates]
        if self.mode == "legacy":
            regions.sort(key=lambda region: (region.bbox.y0, region.bbox.x0))
        return regions, stats

    def _parse_overall_ocr(
        self,
        page_no: int,
        width: float,
        height: float,
        remote_w: float,
        remote_h: float,
        pruned: dict[str, Any],
        stats: dict[str, Any],
    ) -> list[LayoutRegion]:
        overall = pruned.get("overall_ocr_res")
        if not isinstance(overall, dict):
            return []
        texts = overall.get("rec_texts") if isinstance(overall.get("rec_texts"), list) else []
        boxes = overall.get("rec_boxes") or overall.get("rec_polys") or []
        regions: list[LayoutRegion] = []
        for index, text_value in enumerate(texts):
            text = str(text_value).strip()
            if not text:
                continue
            bbox = self.parse_bbox(
                boxes[index] if index < len(boxes) else None,
                width,
                height,
                remote_w,
                remote_h,
            )
            if bbox is None:
                stats["invalid_bbox_count"] += 1
                continue
            stats["labels"]["text"] += 1
            regions.append(
                LayoutRegion(
                    region_type="text",
                    bbox=bbox,
                    page_number=page_no,
                    text=text,
                    original_label="text",
                    layout_order=index + 1,
                )
            )
        return regions

    def _table_entries(
        self,
        pruned: dict[str, Any],
        width: float,
        height: float,
        remote_w: float,
        remote_h: float,
    ) -> list[dict[str, Any]]:
        table_res_list = pruned.get("table_res_list") or pruned.get("tableResList")
        if not isinstance(table_res_list, list):
            return []
        entries: list[dict[str, Any]] = []
        for item in table_res_list:
            if not isinstance(item, dict):
                continue
            raw_cells = item.get("cell_box_list") or item.get("cellBoxList")
            if not isinstance(raw_cells, list):
                continue
            cells: list[list[float]] = []
            for raw_cell in raw_cells:
                bbox = self.parse_bbox(raw_cell, width, height, remote_w, remote_h)
                if bbox is not None:
                    cells.append([bbox.x0, bbox.y0, bbox.x1, bbox.y1])
            if not cells:
                continue
            entries.append({"bbox": union_bbox_values(cells), "cells": cells})
        return entries

    def _match_tables(
        self,
        candidates: list[tuple[LayoutRegion, BBox]],
        entries: list[dict[str, Any]],
    ) -> dict[int, int]:
        scored: list[tuple[float, int, int]] = []
        for candidate_index, (region, bbox) in enumerate(candidates):
            if region.region_type != "table":
                continue
            for entry_index, entry in enumerate(entries):
                score = smaller_coverage(bbox, entry["bbox"])
                if score >= 0.3:
                    scored.append((score, candidate_index, entry_index))
        matches: dict[int, int] = {}
        used_entries: set[int] = set()
        for _, candidate_index, entry_index in sorted(
            scored,
            key=lambda item: (-item[0], item[1], item[2]),
        ):
            if candidate_index in matches or entry_index in used_entries:
                continue
            matches[candidate_index] = entry_index
            used_entries.add(entry_index)
        return matches

    def parse_bbox(
        self,
        value: Any,
        width: float,
        height: float,
        remote_w: float,
        remote_h: float,
    ) -> BBox | None:
        coordinates: tuple[float, float, float, float] | None = None
        try:
            if isinstance(value, dict):
                if all(key in value for key in ("x0", "y0", "x1", "y1")):
                    coordinates = tuple(float(value[key]) for key in ("x0", "y0", "x1", "y1"))
                elif all(key in value for key in ("left", "top", "right", "bottom")):
                    coordinates = tuple(float(value[key]) for key in ("left", "top", "right", "bottom"))
            elif isinstance(value, (list, tuple)) and len(value) >= 4:
                if all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in value):
                    xs = [float(point[0]) for point in value]
                    ys = [float(point[1]) for point in value]
                    coordinates = min(xs), min(ys), max(xs), max(ys)
                else:
                    coordinates = tuple(float(item) for item in value[:4])
        except (TypeError, ValueError):
            return None
        if coordinates is None:
            return None

        left, top, right, bottom = coordinates
        if max(abs(left), abs(top), abs(right), abs(bottom)) <= 1.0:
            left, right = left * width, right * width
            top, bottom = top * height, bottom * height
        elif remote_w > 0 and remote_h > 0 and (remote_w != width or remote_h != height):
            left, right = left * width / remote_w, right * width / remote_w
            top, bottom = top * height / remote_h, bottom * height / remote_h
        left, right = sorted((left, right))
        top, bottom = sorted((top, bottom))
        left, right = max(0.0, left), min(width, right)
        top, bottom = max(0.0, top), min(height, bottom)
        if right <= left or bottom <= top:
            return None
        return BBox(x0=left, y0=top, x1=right, y1=bottom)

    @staticmethod
    def remote_pages(data_info: Any) -> list[tuple[float, float]]:
        if not isinstance(data_info, dict):
            return []
        if data_info.get("type") == "image":
            width, height = data_info.get("width"), data_info.get("height")
            if isinstance(width, (int, float)) and isinstance(height, (int, float)):
                return [(float(width), float(height))]
        pages = data_info.get("pages")
        if not isinstance(pages, list):
            return []
        result: list[tuple[float, float]] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            width, height = page.get("width"), page.get("height")
            if isinstance(width, (int, float)) and isinstance(height, (int, float)):
                result.append((float(width), float(height)))
        return result

    @staticmethod
    def pdf_page_sizes(path: str | Path) -> list[tuple[float, float]]:
        try:
            pdf = fitz.open(path)
        except Exception:
            return []
        try:
            return [(float(page.rect.width), float(page.rect.height)) for page in pdf]
        finally:
            pdf.close()

    @staticmethod
    def clean_text(value: Any) -> str:
        text = str(value or "")
        text = re.sub(r"^#+\s*", "", text.strip())
        return re.sub(r"[ \t]+", " ", text).strip()

    @staticmethod
    def _confidence(item: dict[str, Any]) -> float:
        for key in ("score", "confidence", "block_score"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return max(0.0, min(1.0, float(value)))
        return 1.0

    def _quality(self, **updates: Any) -> LayoutQualityReport:
        parser_version = "v3" if self.mode in {"v3", "v3_shadow"} else LAYOUT_PARSER_VERSION
        report = LayoutQualityReport(parser_version=parser_version, mode=self.mode, **updates)
        if report.invalid_bbox_count:
            report.warnings.append(
                ParseWarningDetail(
                    code="LAYOUT_INVALID_BBOX",
                    message=f"版面分析跳过了 {report.invalid_bbox_count} 个无效坐标区域。",
                    source="layout_analysis",
                )
            )
        if report.table_cell_unmatched_count:
            report.warnings.append(
                ParseWarningDetail(
                    code="LAYOUT_TABLE_CELL_UNMATCHED",
                    message=f"有 {report.table_cell_unmatched_count} 个表格区域未匹配到单元格坐标。",
                    source="layout_analysis",
                )
            )
        return report


def canonical_region_type(label: str) -> str:
    value = (label or "text").strip().lower()
    return LABEL_ALIASES.get(value, value)


def flow_role_for_region(region_type: str) -> str:
    value = canonical_region_type(region_type)
    if value in {"doc_title", "title", "paragraph_title"}:
        return "heading"
    if value in {"header", "footer"}:
        return "margin"
    if value == "footnote":
        return "note"
    if value == "aside_text":
        return "aside"
    if value in {"table", "table_cell"}:
        return "table"
    if value in {"table_title", "table_caption", "table_footnote", "figure_title"}:
        return "caption"
    if value in {"list", "reference", "number"}:
        return "list"
    if value == "abandon":
        return "noise"
    if value in NON_TEXT_REGION_TYPES:
        return "non_text"
    return "body"


def union_bbox_values(values: list[list[float]]) -> BBox:
    return BBox(
        x0=min(value[0] for value in values),
        y0=min(value[1] for value in values),
        x1=max(value[2] for value in values),
        y1=max(value[3] for value in values),
    )


def smaller_coverage(left: BBox, right: BBox) -> float:
    intersection_width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    intersection_height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
    intersection = intersection_width * intersection_height
    smaller = min(bbox_area(left), bbox_area(right))
    return intersection / smaller if smaller > 0 else 0.0


def bbox_area(bbox: BBox) -> float:
    return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)
