"""Geometry grid inference from table cell bounding boxes."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import BBox
from app.models_table import TableCell, TableRow
from app.services.table_compare import utils


@dataclass(frozen=True)
class BBoxGridCell:
    source_row: int
    source_col: int
    row_index: int
    col_index: int
    rowspan: int
    colspan: int
    bbox: BBox


@dataclass(frozen=True)
class BBoxGrid:
    rows: list[tuple[float, float]]
    cols: list[tuple[float, float]]
    cells: list[BBoxGridCell]
    strategy: str


@dataclass
class GridComparison:
    status: str = "not_available"
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)
    strategy: str = ""
    bbox_grid_row_count: int = 0
    bbox_grid_col_count: int = 0
    bbox_cell_count: int = 0
    html_cell_count: int = 0
    bbox_count_matches_html: bool = True
    col_corrections: dict[tuple[int, int], int] = field(default_factory=dict)


class BBoxGridAnalyzer:
    """Builds a conservative bbox alignment network for table cells."""

    _OVERLAP_THRESHOLD = 0.35

    def analyze(
        self,
        rows: list[TableRow],
        html_col_count: int,
        *,
        provided_bbox_count: int = 0,
    ) -> GridComparison:
        all_html_cells = [cell for row in rows for cell in row.cells]
        bbox_cells = [cell for cell in all_html_cells if cell.bbox is not None]
        result = GridComparison(
            html_cell_count=len(all_html_cells),
            bbox_cell_count=len(bbox_cells),
            bbox_count_matches_html=provided_bbox_count in {0, len(all_html_cells)},
        )
        if not bbox_cells:
            result.status = "not_available"
            result.confidence = 0.0
            return result

        grid = self._build_grid(bbox_cells)
        result.strategy = grid.strategy
        result.bbox_grid_row_count = len(grid.rows)
        result.bbox_grid_col_count = len(grid.cols)

        if provided_bbox_count and provided_bbox_count != len(all_html_cells):
            result.warnings.append("bbox_count_mismatch")
            result.confidence = min(result.confidence, 0.55)

        row_delta = abs(len(grid.rows) - len(rows))
        col_delta = abs(len(grid.cols) - html_col_count)
        if row_delta:
            result.warnings.append("bbox_html_row_count_delta")
        if col_delta:
            result.warnings.append("bbox_html_col_count_delta")

        inferred_by_cell = {
            (cell.source_row, cell.source_col): cell
            for cell in grid.cells
        }
        row_mismatches = 0
        col_mismatches = 0
        comparable = 0
        for cell in bbox_cells:
            inferred = inferred_by_cell.get((cell.row_index, cell.col_index))
            if inferred is None:
                continue
            comparable += 1
            if inferred.row_index != cell.row_index:
                row_mismatches += 1
            if inferred.col_index != cell.col_index:
                col_mismatches += 1
                result.col_corrections[(cell.row_index, cell.col_index)] = inferred.col_index

        mismatch_base = max(comparable, 1)
        row_mismatch_ratio = row_mismatches / mismatch_base
        col_mismatch_ratio = col_mismatches / mismatch_base
        if row_mismatches:
            result.warnings.append("bbox_html_row_alignment_conflict")
        if col_mismatches:
            result.warnings.append("bbox_html_col_alignment_conflict")

        if self._has_right_fragment_overflow(rows, html_col_count, len(grid.cols)):
            result.warnings.append("right_fragment_overflow")
            result.confidence = min(result.confidence, 0.7)

        if col_mismatches and row_mismatches == 0 and len(grid.cols) == html_col_count:
            result.status = "minor_conflict"
            result.confidence = min(result.confidence, 0.75)
            return result

        if col_mismatches and row_mismatches == 0 and col_delta <= 1:
            result.status = "low_confidence"
            result.confidence = min(result.confidence, 0.55)
            result.col_corrections = {}
            return result

        if html_col_count >= 6 and len(grid.cols) <= max(2, html_col_count // 3):
            result.status = "geometry_unusable"
            result.warnings.append("bbox_geometry_unusable")
            result.confidence = min(result.confidence, 0.45)
            result.col_corrections = {}
            return result

        if col_delta >= 3 and row_mismatches == 0:
            result.status = "low_confidence"
            result.confidence = min(result.confidence, 0.45)
            result.col_corrections = {}
            return result

        if row_delta >= 2 or col_delta >= 3 or row_mismatch_ratio > 0.25 or col_mismatch_ratio > 0.45:
            result.status = "severe_conflict"
            result.confidence = min(result.confidence, 0.2)
            result.col_corrections = {}
            return result

        if not result.bbox_count_matches_html:
            result.status = "low_confidence"
            result.confidence = min(result.confidence, 0.55)
            result.col_corrections = {}
            return result

        result.status = "consistent" if not result.warnings else "low_confidence"
        if result.status == "low_confidence":
            result.confidence = min(result.confidence, 0.7)
        result.col_corrections = {}
        return result

    def apply_col_corrections(self, rows: list[TableRow], comparison: GridComparison) -> list[TableRow]:
        if comparison.status != "minor_conflict" or not comparison.col_corrections:
            return rows
        corrected_rows: list[TableRow] = []
        for row in rows:
            corrected_cells: list[TableCell] = []
            occupied: set[int] = set()
            for cell in row.cells:
                new_col = comparison.col_corrections.get((cell.row_index, cell.col_index), cell.col_index)
                if new_col in occupied:
                    return rows
                occupied.add(new_col)
                corrected_cells.append(cell.model_copy(update={"col_index": new_col}))
            corrected_cells.sort(key=lambda item: item.col_index)
            corrected_rows.append(TableRow(row_index=row.row_index, cells=corrected_cells))
        return corrected_rows

    def _build_grid(self, cells: list[TableCell]) -> BBoxGrid:
        row_clusters = self._cluster_intervals([(cell.bbox.y0, cell.bbox.y1) for cell in cells if cell.bbox])
        col_clusters = self._cluster_intervals([(cell.bbox.x0, cell.bbox.x1) for cell in cells if cell.bbox])
        grid_cells: list[BBoxGridCell] = []
        for cell in cells:
            if cell.bbox is None:
                continue
            row_indexes = self._overlapping_cluster_indexes(row_clusters, cell.bbox.y0, cell.bbox.y1)
            col_indexes = self._overlapping_cluster_indexes(col_clusters, cell.bbox.x0, cell.bbox.x1)
            if not row_indexes or not col_indexes:
                continue
            grid_cells.append(BBoxGridCell(
                source_row=cell.row_index,
                source_col=cell.col_index,
                row_index=min(row_indexes),
                col_index=min(col_indexes),
                rowspan=max(row_indexes) - min(row_indexes) + 1,
                colspan=max(col_indexes) - min(col_indexes) + 1,
                bbox=cell.bbox,
            ))
        strategy = self._strategy(row_clusters, col_clusters, grid_cells)
        return BBoxGrid(rows=row_clusters, cols=col_clusters, cells=grid_cells, strategy=strategy)

    def _cluster_intervals(self, intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
        clusters: list[tuple[float, float]] = []
        for start, end in sorted(intervals, key=lambda item: ((item[0] + item[1]) / 2, item[0])):
            if end <= start:
                continue
            matched = False
            for index, (cluster_start, cluster_end) in enumerate(clusters):
                if self._overlap_ratio((start, end), (cluster_start, cluster_end)) >= self._OVERLAP_THRESHOLD:
                    clusters[index] = (min(cluster_start, start), max(cluster_end, end))
                    matched = True
                    break
            if not matched:
                clusters.append((start, end))
        return clusters

    def _overlapping_cluster_indexes(
        self,
        clusters: list[tuple[float, float]],
        start: float,
        end: float,
    ) -> list[int]:
        return [
            index
            for index, cluster in enumerate(clusters)
            if self._overlap_ratio((start, end), cluster) >= self._OVERLAP_THRESHOLD
        ]

    @staticmethod
    def _overlap_ratio(left: tuple[float, float], right: tuple[float, float]) -> float:
        overlap = max(0.0, min(left[1], right[1]) - max(left[0], right[0]))
        denom = max(1.0, min(left[1] - left[0], right[1] - right[0]))
        return overlap / denom

    @staticmethod
    def _strategy(
        rows: list[tuple[float, float]],
        cols: list[tuple[float, float]],
        cells: list[BBoxGridCell],
    ) -> str:
        if not rows or not cols:
            return "stream"
        density = len(cells) / max(1, len(rows) * len(cols))
        if density >= 0.65:
            return "lattice"
        if len(cells) >= max(len(rows), len(cols)):
            return "network"
        return "stream"

    @staticmethod
    def _has_right_fragment_overflow(rows: list[TableRow], html_col_count: int, bbox_col_count: int) -> bool:
        if html_col_count < bbox_col_count + 2:
            return False
        for row in rows:
            right_cells = [cell for cell in row.cells if cell.col_index >= bbox_col_count and utils.normalize(cell.text)]
            if not right_cells:
                continue
            if all(len(utils.normalize(cell.text)) <= 3 for cell in right_cells):
                return True
        return False
