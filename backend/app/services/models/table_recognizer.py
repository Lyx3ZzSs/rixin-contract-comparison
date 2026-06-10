from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.models.layout_detector import LayoutRegion, LayoutResult

logger = logging.getLogger(__name__)


@dataclass
class TableStructure:
    """Structured table extracted from a layout region."""

    region: LayoutRegion
    html: str = ""
    cell_bboxes: list[list[float]] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


class TableRecognizer:
    """Extracts table structures from layout detection results.

    Does NOT make additional API calls — parses table data already
    present in ``LayoutRegion`` s returned by ``LayoutDetector``.
    In MinerU terms this mirrors the SLANet / table recognition stage,
    but operates on the PP-Structure response payload.
    """

    name = "table_recognizer"
    device = "cpu"

    TABLE_LABELS = {"table", "table_title", "table_caption"}

    def predict(self, input: Any) -> TableStructure:
        """Extract table structure from a layout region.

        Args:
            input: A ``LayoutRegion`` with region_type == "table".

        Returns:
            ``TableStructure`` with parsed HTML and cell bboxes.
        """
        if not isinstance(input, LayoutRegion):
            raise TypeError(f"TableRecognizer expects LayoutRegion, got {type(input)}")
        return self._extract(input)

    def batch_predict(self, inputs: list[Any], batch_size: int = 8) -> list[TableStructure]:
        return [self.predict(item) for item in inputs]

    def extract_from_layout(self, layout: LayoutResult) -> list[TableStructure]:
        """Convenience: extract all table regions from a LayoutResult."""
        return [
            self._extract(r)
            for r in layout.regions
            if r.region_type in self.TABLE_LABELS
        ]

    def _extract(self, region: LayoutRegion) -> TableStructure:
        html = region.raw_html or region.text if "<table" in (region.text or "").lower() else ""
        rows = self._parse_html_rows(html) if html else []
        return TableStructure(
            region=region,
            html=html,
            cell_bboxes=region.table_cell_bboxes,
            rows=rows,
        )

    @staticmethod
    def _parse_html_rows(html: str) -> list[list[str]]:
        """Quick HTML table → 2D array extraction."""
        import re
        rows: list[list[str]] = []
        for tr_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE):
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr_match.group(1), re.DOTALL | re.IGNORECASE)
            cleaned = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if cleaned:
                rows.append(cleaned)
        return rows

    def unload(self) -> None:
        pass
