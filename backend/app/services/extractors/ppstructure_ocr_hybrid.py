from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import settings
from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.extractors.ppstructure import PPStructureExtractor


class PPStructureOCRHybridExtractor:
    name = "ppstructure_ocr_hybrid"

    def __init__(
        self,
        structure_extractor: PPStructureExtractor | None = None,
        ocr_extractor: PPOCRV5Extractor | None = None,
        overlap_threshold: float | None = None,
    ) -> None:
        self.structure_extractor = structure_extractor or PPStructureExtractor()
        self.ocr_extractor = ocr_extractor or PPOCRV5Extractor()
        self.overlap_threshold = settings.hybrid_layout_overlap_threshold if overlap_threshold is None else overlap_threshold

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        ocr_result = self.ocr_extractor.extract(path, task_id=task_id)
        try:
            structure_result = self.structure_extractor.extract(path, task_id=task_id)
        except DocumentExtractionError as exc:
            ocr_result.extractor_used = "ppstructure_ocr_hybrid_ocr_only"
            ocr_result.warnings.append(f"PP-Structure 结构识别失败，已使用 PP-OCRv5 文本继续处理: {exc}")
            return ocr_result

        document = self._merge_documents(ocr_result.document, structure_result.document)
        raw_result_path = self._merge_raw_paths(structure_result.raw_result_path, ocr_result.raw_result_path)
        if settings.save_ocr_raw_result and settings.hybrid_save_merged_raw and task_id:
            merged_raw_path = self._save_merged_raw(
                document,
                task_id,
                Path(path),
                structure_result.raw_result_path,
                ocr_result.raw_result_path,
            )
            raw_result_path = self._merge_raw_paths(raw_result_path, merged_raw_path)

        return ExtractionResult(
            document=document,
            extractor_used=self.name,
            raw_result_path=raw_result_path,
            warnings=[*structure_result.warnings, *ocr_result.warnings],
        )

    def _merge_documents(self, ocr_document: Document, structure_document: Document) -> Document:
        structure_pages = {page.page_no: page for page in structure_document.pages}
        pages = [
            Page(
                page_no=page.page_no,
                width=page.width,
                height=page.height,
                blocks=self._merge_page_blocks(page.blocks, structure_pages.get(page.page_no)),
            )
            for page in ocr_document.pages
        ]
        return ocr_document.model_copy(update={"pages": pages})

    def _merge_page_blocks(self, ocr_blocks: list[TextBlock], structure_page: Page | None) -> list[TextBlock]:
        if structure_page is None:
            return ocr_blocks
        structure_blocks = [
            block
            for block in structure_page.blocks
            if block.bbox.x1 > block.bbox.x0 and block.bbox.y1 > block.bbox.y0
        ]
        if not structure_blocks:
            return ocr_blocks
        html_tables = self._collect_html_tables(structure_blocks)
        merged = [self._attach_structure(block, structure_blocks) for block in ocr_blocks]
        return self._consolidate_table_blocks(merged, html_tables)

    def _attach_structure(self, ocr_block: TextBlock, structure_blocks: list[TextBlock]) -> TextBlock:
        matched = self._best_structure_match(ocr_block.bbox, structure_blocks)
        if matched is None:
            return ocr_block
        block_type = self._normalize_block_type(matched.block_type) or ocr_block.block_type
        return ocr_block.model_copy(
            update={
                "block_type": block_type,
                "layout_block_id": matched.block_id,
                "layout_order": self._layout_order(matched.block_id),
                "layout_bbox": matched.bbox,
            }
        )

    def _best_structure_match(self, bbox: BBox, structure_blocks: list[TextBlock]) -> TextBlock | None:
        best_block: TextBlock | None = None
        best_coverage = 0.0
        for block in structure_blocks:
            coverage = self._overlap_coverage(bbox, block.bbox)
            if coverage > best_coverage:
                best_coverage = coverage
                best_block = block

        if best_block is not None and best_coverage >= self.overlap_threshold:
            return best_block
        if not settings.hybrid_layout_center_fallback:
            return None
        return self._smallest_center_containing_block(bbox, structure_blocks)

    def _smallest_center_containing_block(self, bbox: BBox, structure_blocks: list[TextBlock]) -> TextBlock | None:
        center_x = (bbox.x0 + bbox.x1) / 2
        center_y = (bbox.y0 + bbox.y1) / 2
        candidates = [
            block
            for block in structure_blocks
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

    @staticmethod
    def _collect_html_tables(structure_blocks: list[TextBlock]) -> dict[str, tuple[str, list[list[float]], BBox]]:
        return {
            b.block_id: (b.text, b.table_cell_bboxes, b.bbox)
            for b in structure_blocks
            if b.block_type in {"table", "table_title", "table_cell"} and "<table" in (b.text or "").lower()
        }

    @staticmethod
    def _consolidate_table_blocks(blocks: list[TextBlock], html_tables: dict[str, tuple[str, list[list[float]], BBox]]) -> list[TextBlock]:
        if not html_tables:
            return blocks
        table_children: dict[str, list[TextBlock]] = {}
        for block in blocks:
            if block.layout_block_id in html_tables:
                table_children.setdefault(block.layout_block_id, []).append(block)

        seen: set[str] = set()
        result: list[TextBlock] = []
        for block in blocks:
            layout_id = block.layout_block_id
            if layout_id in html_tables:
                if layout_id in seen:
                    continue
                seen.add(layout_id)
                html_text, cell_bboxes, layout_bbox = html_tables[layout_id]
                merged_text, merged_char_boxes = PPStructureOCRHybridExtractor._merge_table_ocr_children(
                    table_children.get(layout_id, [block])
                )
                update = {
                    "bbox": layout_bbox,
                    "raw_html": html_text,
                    "table_cell_bboxes": cell_bboxes,
                }
                if merged_text:
                    update["text"] = merged_text
                if merged_char_boxes:
                    update["char_boxes"] = merged_char_boxes
                block = block.model_copy(update=update)
            result.append(block)
        return result

    @staticmethod
    def _merge_table_ocr_children(blocks: list[TextBlock]) -> tuple[str, list[CharBox]]:
        parts: list[str] = []
        char_boxes: list[CharBox] = []
        offset = 0

        for block in blocks:
            text = block.text or ""
            if not text:
                continue
            parts.append(text)
            for local_index, char_box in enumerate(block.char_boxes):
                source_index = char_box.text_index if char_box.text_index is not None else local_index
                char_boxes.append(char_box.model_copy(update={"text_index": offset + source_index}))
            offset += len(text) + 1

        return "\n".join(parts), char_boxes

    def _normalize_block_type(self, block_type: str) -> str:
        value = (block_type or "").strip().lower()
        if value in {"table", "table_title", "table_caption"}:
            return "table"
        if value in {"header", "page_header"}:
            return "header"
        if value in {"footer", "page_footer"}:
            return "footer"
        if value in {"doc_title", "title"}:
            return value
        if value in {"paragraph", "text", "content", "list", "reference"}:
            return "text"
        return value

    def _save_merged_raw(
        self,
        document: Document,
        task_id: str,
        source_path: Path,
        structure_raw_path: str,
        ocr_raw_path: str,
    ) -> str:
        directory = settings.ocr_dir / task_id
        directory.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", source_path.stem)[:80] or "document"
        path = directory / f"{stem}_ppstructure_ocr_hybrid_raw.json"
        path.write_text(
            json.dumps(
                {
                    "structure_raw_path": structure_raw_path,
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
