from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.models import BBox, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.paddleocr_vl import PaddleOCRVLExtractor
from app.services.extractors.ppstructure import PPStructureExtractor


class HybridOCRStructureExtractor:
    name = "hybrid"

    def __init__(
        self,
        ocr_extractor: PaddleOCRVLExtractor | None = None,
        structure_extractor: PPStructureExtractor | None = None,
        overlap_threshold: float | None = None,
    ) -> None:
        self.ocr_extractor = ocr_extractor or PaddleOCRVLExtractor()
        self.structure_extractor = structure_extractor or PPStructureExtractor()
        self.overlap_threshold = (
            settings.hybrid_layout_overlap_threshold if overlap_threshold is None else overlap_threshold
        )

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        ocr_result = self.ocr_extractor.extract(path, task_id=task_id)
        try:
            structure_result = self.structure_extractor.extract(path, task_id=task_id)
        except DocumentExtractionError as exc:
            ocr_result.extractor_used = "hybrid_ocr_only"
            ocr_result.warnings.append(f"PP-Structure 版面识别失败，已使用 PaddleOCR-VL-1.5 文本继续处理: {exc}")
            return ocr_result

        document = self._merge_documents(ocr_result.document, structure_result.document)
        warnings = [*ocr_result.warnings, *structure_result.warnings]
        raw_result_path = self._merge_raw_paths(ocr_result.raw_result_path, structure_result.raw_result_path)
        return ExtractionResult(
            document=document,
            extractor_used=self.name,
            raw_result_path=raw_result_path,
            warnings=warnings,
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
        return [self._tag_ocr_block(block, structure_blocks) for block in ocr_blocks]

    def _tag_ocr_block(self, ocr_block: TextBlock, structure_blocks: list[TextBlock]) -> TextBlock:
        matched = self._best_structure_match(ocr_block.bbox, structure_blocks)
        if matched is None:
            return ocr_block
        return ocr_block.model_copy(update={"block_type": matched.block_type or ocr_block.block_type})

    def _best_structure_match(self, bbox: BBox, structure_blocks: list[TextBlock]) -> TextBlock | None:
        best_block: TextBlock | None = None
        best_coverage = 0.0
        for block in structure_blocks:
            coverage = self._overlap_coverage(bbox, block.bbox)
            if coverage > best_coverage:
                best_coverage = coverage
                best_block = block
        if best_block is None or best_coverage < self.overlap_threshold:
            return None
        return best_block

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

    def _merge_raw_paths(self, *paths: str) -> str:
        return "\n".join(path for path in paths if path)
