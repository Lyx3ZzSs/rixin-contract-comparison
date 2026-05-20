from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import settings
from app.models import BBox, Document, Page, TextBlock
from app.services.extractors.base import ExtractionResult
from app.services.extractors.paddleocr_vl import PaddleOCRVLExtractor
from app.services.extractors.ppocrv5 import PPOCRV5Extractor


class VLOCRHybridExtractor:
    name = "vl_ocr_hybrid"

    def __init__(
        self,
        layout_extractor: PaddleOCRVLExtractor | None = None,
        ocr_extractor: PPOCRV5Extractor | None = None,
        overlap_threshold: float | None = None,
    ) -> None:
        self.layout_extractor = layout_extractor or PaddleOCRVLExtractor()
        self.ocr_extractor = ocr_extractor or PPOCRV5Extractor()
        self.overlap_threshold = settings.hybrid_layout_overlap_threshold if overlap_threshold is None else overlap_threshold

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        path = Path(path)
        layout_result = self.layout_extractor.extract(path, task_id=task_id)
        ocr_result = self.ocr_extractor.extract(path, task_id=task_id)

        document = self._merge_documents(ocr_result.document, layout_result.document)
        raw_result_path = self._merge_raw_paths(layout_result.raw_result_path, ocr_result.raw_result_path)
        if settings.save_ocr_raw_result and settings.hybrid_save_merged_raw and task_id:
            merged_raw_path = self._save_merged_raw(document, task_id, path, layout_result.raw_result_path, ocr_result.raw_result_path)
            raw_result_path = self._merge_raw_paths(raw_result_path, merged_raw_path)

        return ExtractionResult(
            document=document,
            extractor_used=self.name,
            raw_result_path=raw_result_path,
            warnings=[*layout_result.warnings, *ocr_result.warnings],
        )

    def _merge_documents(self, ocr_document: Document, layout_document: Document) -> Document:
        layout_pages = {page.page_no: page for page in layout_document.pages}
        pages = [
            Page(
                page_no=page.page_no,
                width=page.width,
                height=page.height,
                blocks=self._merge_page_blocks(page.blocks, layout_pages.get(page.page_no)),
            )
            for page in ocr_document.pages
        ]
        return ocr_document.model_copy(update={"pages": pages})

    def _merge_page_blocks(self, ocr_blocks: list[TextBlock], layout_page: Page | None) -> list[TextBlock]:
        if layout_page is None:
            return ocr_blocks
        layout_blocks = [
            block
            for block in layout_page.blocks
            if block.bbox.x1 > block.bbox.x0 and block.bbox.y1 > block.bbox.y0
        ]
        if not layout_blocks:
            return ocr_blocks
        return [self._attach_layout(block, layout_blocks) for block in ocr_blocks]

    def _attach_layout(self, ocr_block: TextBlock, layout_blocks: list[TextBlock]) -> TextBlock:
        matched = self._best_layout_match(ocr_block.bbox, layout_blocks)
        if matched is None:
            return ocr_block
        return ocr_block.model_copy(
            update={
                "block_type": matched.block_type or ocr_block.block_type,
                "layout_block_id": matched.block_id,
                "layout_order": self._layout_order(matched.block_id),
                "layout_bbox": matched.bbox,
            }
        )

    def _best_layout_match(self, bbox: BBox, layout_blocks: list[TextBlock]) -> TextBlock | None:
        best_block: TextBlock | None = None
        best_coverage = 0.0
        for block in layout_blocks:
            coverage = self._overlap_coverage(bbox, block.bbox)
            if coverage > best_coverage:
                best_coverage = coverage
                best_block = block

        if best_block is not None and best_coverage >= self.overlap_threshold:
            return best_block
        if not settings.hybrid_layout_center_fallback:
            return None
        return self._smallest_center_containing_block(bbox, layout_blocks)

    def _smallest_center_containing_block(self, bbox: BBox, layout_blocks: list[TextBlock]) -> TextBlock | None:
        center_x = (bbox.x0 + bbox.x1) / 2
        center_y = (bbox.y0 + bbox.y1) / 2
        candidates = [
            block
            for block in layout_blocks
            if block.bbox.x0 <= center_x <= block.bbox.x1 and block.bbox.y0 <= center_y <= block.bbox.y1
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda block: self._area(block.bbox))

    def _overlap_coverage(self, inner: BBox, outer: BBox) -> float:
        inner_area = self._area(inner)
        if inner_area <= 0:
            return 0.0
        x0 = max(inner.x0, outer.x0)
        y0 = max(inner.y0, outer.y0)
        x1 = min(inner.x1, outer.x1)
        y1 = min(inner.y1, outer.y1)
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return ((x1 - x0) * (y1 - y0)) / inner_area

    def _area(self, bbox: BBox) -> float:
        return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)

    def _layout_order(self, block_id: str) -> int | None:
        match = re.search(r"_b(\d+)$", block_id or "")
        return int(match.group(1)) if match else None

    def _save_merged_raw(
        self,
        document: Document,
        task_id: str,
        source_path: Path,
        layout_raw_path: str,
        ocr_raw_path: str,
    ) -> str:
        directory = settings.ocr_dir / task_id
        directory.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", source_path.stem)[:80] or "document"
        path = directory / f"{stem}_vl_ocr_hybrid_raw.json"
        path.write_text(
            json.dumps(
                {
                    "layout_raw_path": layout_raw_path,
                    "ocr_raw_path": ocr_raw_path,
                    "document": document.model_dump(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(path)

    def _merge_raw_paths(self, *paths: str) -> str:
        return "\n".join(path for path in paths if path)
