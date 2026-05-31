from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz

from app.models import BBox
from app.services.models.remote_model import RemoteModel

logger = logging.getLogger(__name__)

# Region types returned by PP-Structure layout-parsing API.
REGION_TYPES = {
    "text",
    "title",
    "doc_title",
    "table",
    "table_title",
    "table_caption",
    "table_footnote",
    "header",
    "page_header",
    "footer",
    "page_footer",
    "seal",
    "figure",
    "figure_caption",
    "figure_title",
    "list",
    "reference",
    "abandon",
}


@dataclass
class LayoutRegion:
    """A single detected region on a page."""

    region_type: str
    bbox: BBox
    page_number: int
    confidence: float = 1.0
    text: str = ""
    raw_html: str = ""
    table_cell_bboxes: list[list[float]] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class LayoutResult:
    """Layout detection result for an entire document."""

    regions: list[LayoutRegion] = field(default_factory=list)
    page_dimensions: dict[int, tuple[float, float]] = field(default_factory=dict)
    page_count: int = 0


class LayoutDetector(RemoteModel):
    """Wraps PP-Structure ``/layout-parsing`` endpoint.

    Calls the remote API once per document, then parses the response
    into a structured ``LayoutResult`` with typed ``LayoutRegion`` s.

    This is the first step in the multi-model pipeline (mirrors MinerU's
    layout detection stage).
    """

    def __init__(
        self,
        base_url: str,
        access_token: str = "",
        timeout: int = 600,
        use_table_recognition: bool = True,
        use_seal_recognition: bool = True,
        use_region_detection: bool = True,
        format_block_content: bool = True,
        use_doc_orientation_classify: bool = False,
        use_doc_unwarping: bool = False,
        use_textline_orientation: bool = False,
    ) -> None:
        super().__init__(
            name="layout_detector",
            base_url=base_url,
            access_token=access_token,
            timeout=timeout,
        )
        self._use_table_recognition = use_table_recognition
        self._use_seal_recognition = use_seal_recognition
        self._use_region_detection = use_region_detection
        self._format_block_content = format_block_content
        self._use_doc_orientation_classify = use_doc_orientation_classify
        self._use_doc_unwarping = use_doc_unwarping
        self._use_textline_orientation = use_textline_orientation

    # -- RemoteModel overrides ------------------------------------------

    def predict(self, input: Any) -> LayoutResult:
        """Detect layout regions in a PDF document.

        Args:
            input: A ``Path`` or ``bytes`` of a PDF file.

        Returns:
            ``LayoutResult`` with all detected regions.
        """
        path = self._resolve_path(input)
        page_sizes = self._pdf_page_sizes(path)
        body = self._request_body(path)
        payload = self._post("/layout-parsing", body)
        return self._parse_response(payload, page_sizes)

    # -- Request building -----------------------------------------------

    def _resolve_path(self, input: Any) -> Path:
        if isinstance(input, Path):
            return input
        if isinstance(input, (str, bytes)):
            path = Path(input) if isinstance(input, str) else Path(str(input))
            return path
        raise TypeError(f"LayoutDetector.predict expects Path or str, got {type(input)}")

    def _request_body(self, path: Path) -> dict[str, Any]:
        return {
            "file": self._encode_file(path),
            "fileType": 0,
            "useDocOrientationClassify": self._use_doc_orientation_classify,
            "useDocUnwarping": self._use_doc_unwarping,
            "useTextlineOrientation": self._use_textline_orientation,
            "useTableRecognition": self._use_table_recognition,
            "useSealRecognition": self._use_seal_recognition,
            "useRegionDetection": self._use_region_detection,
            "formatBlockContent": self._format_block_content,
            "visualize": False,
        }

    # -- Response parsing -----------------------------------------------

    def _parse_response(
        self,
        payload: dict[str, Any],
        page_sizes: list[tuple[float, float]],
    ) -> LayoutResult:
        result = payload.get("result")
        if not isinstance(result, dict):
            return LayoutResult(page_count=len(page_sizes))

        page_results = result.get("layoutParsingResults") or result.get("layout_parsing_results") or []
        remote_sizes = self._remote_pages(result.get("dataInfo"))
        page_count = max(len(page_sizes), len(page_results), 1)

        regions: list[LayoutRegion] = []
        page_dimensions: dict[int, tuple[float, float]] = {}

        for page_index in range(page_count):
            page_no = page_index + 1
            width, height = page_sizes[page_index] if page_index < len(page_sizes) else (595.0, 842.0)
            remote_w, remote_h = remote_sizes[page_index] if page_index < len(remote_sizes) else (width, height)
            page_dimensions[page_no] = (width, height)

            page_payload = page_results[page_index] if page_index < len(page_results) else {}
            page_regions = self._parse_page(page_no, width, height, remote_w, remote_h, page_payload)
            regions.extend(page_regions)

        return LayoutResult(regions=regions, page_dimensions=page_dimensions, page_count=page_count)

    def _parse_page(
        self,
        page_no: int,
        width: float,
        height: float,
        remote_w: float,
        remote_h: float,
        page_payload: Any,
    ) -> list[LayoutRegion]:
        if not isinstance(page_payload, dict):
            return []
        pruned = page_payload.get("prunedResult") or page_payload.get("pruned_result") or page_payload
        if not isinstance(pruned, dict):
            return []

        items = pruned.get("parsing_res_list") or pruned.get("parsingResList")
        if not isinstance(items, list):
            return self._parse_overall_ocr(page_no, width, height, remote_w, remote_h, pruned)

        table_res = self._extract_table_res_list(pruned, width, height, remote_w, remote_h)
        used_table_res: set[int] = set()
        regions: list[LayoutRegion] = []

        for index, item in enumerate(items):
            region = self._region_from_item(page_no, width, height, remote_w, remote_h, item, index)
            if region is None:
                continue
            # Attach table cell bboxes for table regions.
            label = str(item.get("block_label") or item.get("label") or "text")
            if label == "table":
                raw_bbox = item.get("block_bbox") or item.get("bbox")
                matched = self._match_table_entry(raw_bbox, table_res, used_table_res)
                if matched is not None:
                    region = LayoutRegion(
                        region_type=region.region_type,
                        bbox=region.bbox,
                        page_number=region.page_number,
                        confidence=region.confidence,
                        text=region.text,
                        raw_html=region.text if "<table" in (region.text or "").lower() else "",
                        table_cell_bboxes=table_res[matched]["cells"],
                        raw_data=region.raw_data,
                    )
            regions.append(region)

        return sorted(regions, key=lambda r: (r.bbox.y0, r.bbox.x0))

    def _region_from_item(
        self,
        page_no: int,
        width: float,
        height: float,
        remote_w: float,
        remote_h: float,
        item: Any,
        index: int,
    ) -> LayoutRegion | None:
        if not isinstance(item, dict):
            return None
        text = self._clean_text(item.get("block_content") or item.get("content") or item.get("text") or "")
        label = str(item.get("block_label") or item.get("label") or "text").strip().lower()
        raw_bbox = item.get("block_bbox") or item.get("bbox")
        bbox = self._parse_bbox(raw_bbox, width, height, remote_w, remote_h)
        return LayoutRegion(
            region_type=label,
            bbox=bbox,
            page_number=page_no,
            confidence=1.0,
            text=text,
            raw_data=dict(item) if isinstance(item, dict) else {},
        )

    def _parse_overall_ocr(
        self,
        page_no: int,
        width: float,
        height: float,
        remote_w: float,
        remote_h: float,
        pruned: dict[str, Any],
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
            raw_bbox = boxes[index] if index < len(boxes) else None
            bbox = self._parse_bbox(raw_bbox, width, height, remote_w, remote_h)
            regions.append(LayoutRegion(region_type="text", bbox=bbox, page_number=page_no, text=text))
        return sorted(regions, key=lambda r: (r.bbox.y0, r.bbox.x0))

    # -- Table cell extraction ------------------------------------------

    def _extract_table_res_list(
        self,
        pruned: dict[str, Any],
        width: float,
        height: float,
        remote_w: float,
        remote_h: float,
    ) -> list[dict[str, Any]]:
        table_res_list = pruned.get("table_res_list")
        if not isinstance(table_res_list, list):
            return []
        result: list[dict[str, Any]] = []
        for table_res in table_res_list:
            cell_box_list = table_res.get("cell_box_list")
            if not isinstance(cell_box_list, list):
                result.append({"raw_bbox": None, "cells": []})
                continue
            raw_xs: list[float] = []
            raw_ys: list[float] = []
            converted: list[list[float]] = []
            for box in cell_box_list:
                if isinstance(box, (list, tuple)) and len(box) >= 4:
                    raw_xs.extend([float(box[0]), float(box[2])])
                    raw_ys.extend([float(box[1]), float(box[3])])
                    bbox = self._clamp(float(box[0]), float(box[1]), float(box[2]), float(box[3]), width, height, remote_w, remote_h)
                    converted.append([bbox.x0, bbox.y0, bbox.x1, bbox.y1])
            raw_bbox = [min(raw_xs), min(raw_ys), max(raw_xs), max(raw_ys)] if raw_xs else None
            result.append({"raw_bbox": raw_bbox, "cells": converted})
        return result

    @staticmethod
    def _match_table_entry(raw_block_bbox: Any, entries: list[dict[str, Any]], used: set[int]) -> int | None:
        if not isinstance(raw_block_bbox, (list, tuple)) or len(raw_block_bbox) < 4:
            return None
        bx0, by0, bx1, by1 = (float(v) for v in raw_block_bbox[:4])
        best_idx: int | None = None
        best_overlap = 0.0
        for i, entry in enumerate(entries):
            if i in used:
                continue
            rb = entry.get("raw_bbox")
            if rb is None:
                continue
            ox0, oy0 = max(bx0, rb[0]), max(by0, rb[1])
            ox1, oy1 = min(bx1, rb[2]), min(by1, rb[3])
            if ox1 <= ox0 or oy1 <= oy0:
                continue
            overlap_area = (ox1 - ox0) * (oy1 - oy0)
            block_area = max((bx1 - bx0) * (by1 - by0), 1.0)
            coverage = overlap_area / block_area
            if coverage > best_overlap:
                best_overlap = coverage
                best_idx = i
        return best_idx if best_overlap > 0.3 else None

    # -- BBox helpers ---------------------------------------------------

    def _parse_bbox(self, value: Any, width: float, height: float, remote_w: float, remote_h: float) -> BBox:
        if isinstance(value, dict):
            if all(k in value for k in ("x0", "y0", "x1", "y1")):
                return self._clamp(value["x0"], value["y0"], value["x1"], value["y1"], width, height, remote_w, remote_h)
            if all(k in value for k in ("left", "top", "right", "bottom")):
                return self._clamp(value["left"], value["top"], value["right"], value["bottom"], width, height, remote_w, remote_h)
        if isinstance(value, (list, tuple)) and len(value) >= 4:
            if all(isinstance(p, (list, tuple)) and len(p) >= 2 for p in value):
                xs = [float(p[0]) for p in value]
                ys = [float(p[1]) for p in value]
                return self._clamp(min(xs), min(ys), max(xs), max(ys), width, height, remote_w, remote_h)
            return self._clamp(value[0], value[1], value[2], value[3], width, height, remote_w, remote_h)
        return BBox(x0=0, y0=0, x1=width, y1=height)

    @staticmethod
    def _clamp(x0: float, y0: float, x1: float, y1: float, w: float, h: float, rw: float, rh: float) -> BBox:
        left, top, right, bottom = float(x0), float(y0), float(x1), float(y1)
        if max(left, top, right, bottom) <= 1.0:
            left *= w
            right *= w
            top *= h
            bottom *= h
        elif rw > 0 and rh > 0 and (rw != w or rh != h):
            left *= w / rw
            right *= w / rw
            top *= h / rh
            bottom *= h / rh
        left = max(0.0, min(left, w))
        top = max(0.0, min(top, h))
        right = max(left, min(right, w))
        bottom = max(top, min(bottom, h))
        return BBox(x0=left, y0=top, x1=right, y1=bottom)

    # -- Utility --------------------------------------------------------

    @staticmethod
    def _pdf_page_sizes(path: Path) -> list[tuple[float, float]]:
        try:
            pdf = fitz.open(path)
        except Exception:
            return []
        try:
            return [(float(p.rect.width), float(p.rect.height)) for p in pdf]
        finally:
            pdf.close()

    @staticmethod
    def _remote_pages(data_info: Any) -> list[tuple[int, int]]:
        if not isinstance(data_info, dict):
            return []
        if data_info.get("type") == "image":
            w, h = data_info.get("width"), data_info.get("height")
            if isinstance(w, int) and isinstance(h, int):
                return [(w, h)]
        pages = data_info.get("pages")
        if not isinstance(pages, list):
            return []
        result: list[tuple[int, int]] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            w, h = page.get("width"), page.get("height")
            if isinstance(w, int) and isinstance(h, int):
                result.append((w, h))
        return result

    @staticmethod
    def _clean_text(value: Any) -> str:
        import re
        text = str(value or "")
        text = re.sub(r"^#+\s*", "", text.strip())
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()
