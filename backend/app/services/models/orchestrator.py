from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError
from app.services.models.layout_detector import LayoutDetector, LayoutRegion, LayoutResult
from app.services.models.registry import ModelRegistry

logger = logging.getLogger(__name__)


@dataclass
class PageExtractionResult:
    """Aggregated extraction result for a single page."""

    page_no: int
    width: float
    height: float
    regions: list[LayoutRegion] = field(default_factory=list)
    text_blocks: list[TextBlock] = field(default_factory=list)
    tables: list[Any] = field(default_factory=list)
    seals: list[Any] = field(default_factory=list)


@dataclass
class OrchestratorResult:
    """Full document extraction result from the multi-model pipeline."""

    document: Document
    layout: LayoutResult
    page_results: list[PageExtractionResult] = field(default_factory=list)


class ModelOrchestrator:
    """Multi-model coordinator — layout detection → region routing → aggregation.

    Mirrors MinerU's ``MineruHybridModel`` pipeline:
    1. Layout detection identifies regions (text, table, seal, header, footer).
    2. Table structures are extracted from table regions.
    3. Seal/signature regions are identified.
    4. Fine-grained OCR enriches text regions.
    5. Results are aggregated into a unified ``Document``.
    """

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self._registry = registry or ModelRegistry.get_instance()

    def process(self, pdf_path: Path, *, task_id: str | None = None) -> OrchestratorResult:
        """Run the full multi-model pipeline on a PDF.

        Args:
            pdf_path: Path to the PDF file.
            task_id: Optional task ID for artifact tracking.

        Returns:
            ``OrchestratorResult`` with document, layout, and per-page details.
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise DocumentExtractionError(f"文件不存在: {pdf_path}")

        # Step 1: Layout detection
        layout_detector = self._registry.get_model("layout_detector")
        layout = layout_detector.predict(pdf_path)

        # Step 2: Table recognition
        tables_by_page: dict[int, list[Any]] = {}
        try:
            table_rec = self._registry.get_model("table_recognizer")
            tables_by_page = self._extract_tables(table_rec, layout)
        except KeyError:
            logger.debug("table_recognizer not registered, skipping")

        # Step 3: Seal detection
        seals_by_page: dict[int, list[Any]] = {}
        try:
            seal_det = self._registry.get_model("seal_detector")
            seals = seal_det.predict(layout)
            for seal in seals:
                seals_by_page.setdefault(seal.page_number, []).append(seal)
        except KeyError:
            logger.debug("seal_detector not registered, skipping")

        # Step 4: Aggregate into Document
        document = self._build_document(pdf_path, layout, tables_by_page)
        page_results = self._build_page_results(layout, tables_by_page, seals_by_page)

        return OrchestratorResult(
            document=document,
            layout=layout,
            page_results=page_results,
        )

    def _extract_tables(self, table_rec: Any, layout: LayoutResult) -> dict[int, list[Any]]:
        """Group table regions by page and extract structures."""
        result: dict[int, list[Any]] = {}
        for region in layout.regions:
            if region.region_type in {"table", "table_title", "table_caption"}:
                table = table_rec.predict(region)
                result.setdefault(region.page_number, []).append(table)
        return result

    def _build_document(
        self,
        pdf_path: Path,
        layout: LayoutResult,
        tables_by_page: dict[int, list[Any]],
    ) -> Document:
        """Convert layout regions into a ``Document`` with ``TextBlock`` s."""
        pages: list[Page] = []
        for page_no in sorted(layout.page_dimensions.keys()):
            width, height = layout.page_dimensions[page_no]
            page_regions = [r for r in layout.regions if r.page_number == page_no]
            blocks = self._regions_to_blocks(page_no, page_regions, tables_by_page.get(page_no, []))
            pages.append(Page(page_no=page_no, width=width, height=height, blocks=blocks))

        return Document(
            filename=pdf_path.name,
            path=str(pdf_path),
            page_count=layout.page_count,
            pages=pages,
        )

    @staticmethod
    def _regions_to_blocks(
        page_no: int,
        regions: list[LayoutRegion],
        tables: list[Any],
    ) -> list[TextBlock]:
        """Convert layout regions to TextBlocks for the Document model."""
        blocks: list[TextBlock] = []
        table_html_map: dict[int, str] = {}

        # Build a map from region index to table HTML.
        for table in tables:
            region = getattr(table, "region", None)
            if region is not None:
                table_html_map[id(region)] = table.html or ""

        for index, region in enumerate(regions):
            block_type = _normalize_block_type(region.region_type)
            raw_html = table_html_map.get(id(region), "")
            cell_bboxes = region.table_cell_bboxes if region.region_type == "table" else []

            block = TextBlock(
                block_id=f"p{page_no}_orchestrator_b{index + 1}",
                page_no=page_no,
                text=region.text,
                bbox=region.bbox,
                block_type=block_type,
                raw_html=raw_html,
                table_cell_bboxes=cell_bboxes,
            )
            blocks.append(block)

        return sorted(blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))

    @staticmethod
    def _build_page_results(
        layout: LayoutResult,
        tables_by_page: dict[int, list[Any]],
        seals_by_page: dict[int, list[Any]],
    ) -> list[PageExtractionResult]:
        results: list[PageExtractionResult] = []
        for page_no in sorted(layout.page_dimensions.keys()):
            width, height = layout.page_dimensions[page_no]
            page_regions = [r for r in layout.regions if r.page_number == page_no]
            results.append(PageExtractionResult(
                page_no=page_no,
                width=width,
                height=height,
                regions=page_regions,
                tables=tables_by_page.get(page_no, []),
                seals=seals_by_page.get(page_no, []),
            ))
        return results


def _normalize_block_type(region_type: str) -> str:
    """Normalize PP-Structure block labels to canonical types."""
    value = (region_type or "").strip().lower()
    if value in {"table", "table_title", "table_caption"}:
        return "table"
    if value in {"header", "page_header"}:
        return "header"
    if value in {"footer", "page_footer"}:
        return "footer"
    if value in {"doc_title", "title"}:
        return "title"
    if value in {"paragraph", "text", "content", "list", "reference"}:
        return "text"
    return value
