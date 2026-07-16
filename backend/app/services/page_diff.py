from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.models import BBox, DiffItem, DiffType, Document, EvidenceBox, Page, TextBlock
from app.utils.id_utils import generate_diff_id


@dataclass(frozen=True)
class _PageCandidate:
    page: Page
    blocks: list[TextBlock]
    text: str
    compact_text: str
    bbox: BBox


@dataclass(frozen=True)
class _PageCoverage:
    diff_type: DiffType
    page_no: int
    bbox: BBox
    compact_text: str


class PageDiffConsolidator:
    """Promotes one-sided full-page changes above duplicate structural diffs."""

    duplicate_sources = {"clause", "table", "metadata", "header_footer"}
    aggregate_sources = {"clause", "table", "metadata", "header_footer"}
    match_anchor_sources = {"clause", "table", "metadata"}
    min_compact_text_length = 80
    max_page_text_length = 20_000
    min_block_count = 2
    min_content_area_ratio = 0.18
    min_block_coverage_ratio = 0.65
    min_weighted_coverage_ratio = 0.82

    noise_block_types = {"abandon", "formula"}
    footer_block_types = {"footer", "page_footer"}

    def consolidate(self, original: Document, compare: Document, diffs: list[DiffItem]) -> list[DiffItem]:
        page_diffs = self._build_page_diffs(original, compare, diffs, self._next_diff_index(diffs))
        if not page_diffs:
            return diffs

        coverage = self._coverage(page_diffs)
        kept = [diff for diff in diffs if not self._covered_duplicate(diff, coverage)]
        return [*kept, *page_diffs]

    def _build_page_diffs(
        self,
        original: Document,
        compare: Document,
        diffs: list[DiffItem],
        start_index: int,
    ) -> list[DiffItem]:
        original_by_page = {candidate.page.page_no: candidate for candidate in self._candidate_pages(original)}
        compare_by_page = {candidate.page.page_no: candidate for candidate in self._candidate_pages(compare)}
        blocked_original, blocked_compare = self._matched_pages(diffs)
        deleted_evidence = self._one_sided_evidence_by_page(diffs, "DELETE")
        added_evidence = self._one_sided_evidence_by_page(diffs, "ADD")

        diffs: list[DiffItem] = []
        next_index = start_index
        for page_no, evidences in sorted(deleted_evidence.items()):
            candidate = original_by_page.get(page_no)
            if candidate and page_no not in blocked_original and self._evidence_covers_page(candidate, evidences):
                diffs.append(self._make_page_diff(candidate, "DELETE", next_index))
                next_index += 1
        for page_no, evidences in sorted(added_evidence.items()):
            candidate = compare_by_page.get(page_no)
            if candidate and page_no not in blocked_compare and self._evidence_covers_page(candidate, evidences):
                diffs.append(self._make_page_diff(candidate, "ADD", next_index))
                next_index += 1
        return diffs

    def _candidate_pages(self, document: Document) -> list[_PageCandidate]:
        candidates: list[_PageCandidate] = []
        for page in document.pages:
            blocks = [block for block in page.blocks if self._is_meaningful_block(block, page)]
            if len(blocks) < self.min_block_count:
                continue
            text = "\n".join((block.text or "").strip() for block in blocks if (block.text or "").strip())
            compact_text = self._compact(text)
            if len(compact_text) < self.min_compact_text_length:
                continue
            bbox = self._union_bbox([block.bbox for block in blocks], page)
            if self._area_ratio(bbox, page) < self.min_content_area_ratio:
                continue
            candidates.append(_PageCandidate(page=page, blocks=blocks, text=text, compact_text=compact_text, bbox=bbox))
        return candidates

    def _matched_pages(self, diffs: list[DiffItem]) -> tuple[set[int], set[int]]:
        original_pages: set[int] = set()
        compare_pages: set[int] = set()
        for diff in diffs:
            if diff.diff_type != "MODIFY" or diff.source_type not in self.match_anchor_sources:
                continue
            if not diff.original_evidence or not diff.compare_evidence:
                continue
            original_pages.update(evidence.page_no for evidence in diff.original_evidence)
            compare_pages.update(evidence.page_no for evidence in diff.compare_evidence)
        return original_pages, compare_pages

    def _one_sided_evidence_by_page(
        self,
        diffs: list[DiffItem],
        diff_type: DiffType,
    ) -> dict[int, list[EvidenceBox]]:
        grouped: dict[int, list[EvidenceBox]] = {}
        for diff in diffs:
            if diff.diff_type != diff_type or diff.source_type not in self.aggregate_sources:
                continue
            evidences = diff.compare_evidence if diff_type == "ADD" else diff.original_evidence
            for evidence in evidences:
                if evidence.highlight_type and evidence.highlight_type != diff_type:
                    continue
                grouped.setdefault(evidence.page_no, []).append(evidence)
        return grouped

    def _evidence_covers_page(self, candidate: _PageCandidate, evidences: list[EvidenceBox]) -> bool:
        if not evidences:
            return False
        total_weight = 0.0
        covered_weight = 0.0
        for block in candidate.blocks:
            weight = self._block_weight(block)
            total_weight += weight
            covered_weight += weight * self._block_coverage_ratio(block.bbox, evidences)
        return bool(total_weight and covered_weight / total_weight >= self.min_weighted_coverage_ratio)

    def _make_page_diff(self, candidate: _PageCandidate, diff_type: DiffType, index: int) -> DiffItem:
        page_label = f"第{candidate.page.page_no}页"
        text = candidate.text[: self.max_page_text_length]
        snippet = text[:300]
        evidence = EvidenceBox(
            page_no=candidate.page.page_no,
            bbox=candidate.bbox,
            method="page_region",
            text=text[:500],
            highlight_type=diff_type,
            confidence=0.9,
            evidence_quality="HIGH",
        )
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=diff_type,
            title=f"整页{'新增' if diff_type == 'ADD' else '删除'}：{page_label}",
            original_text=text if diff_type == "DELETE" else "",
            compare_text=text if diff_type == "ADD" else "",
            original_snippet=snippet if diff_type == "DELETE" else "",
            compare_snippet=snippet if diff_type == "ADD" else "",
            readable_change=f"整页{'新增' if diff_type == 'ADD' else '删除'}：{page_label}",
            source_type="page",
            structural_flags=["PAGE_LEVEL_CHANGE"],
            original_evidence=[evidence] if diff_type == "DELETE" else [],
            compare_evidence=[evidence] if diff_type == "ADD" else [],
        )

    def _covered_duplicate(self, diff: DiffItem, coverage: list[_PageCoverage]) -> bool:
        if diff.source_type not in self.duplicate_sources:
            return False
        evidence = diff.compare_evidence if diff.diff_type == "ADD" else diff.original_evidence
        if diff.diff_type == "MODIFY" or not evidence:
            return False
        relevant = [item for item in coverage if item.diff_type == diff.diff_type]
        if not relevant:
            return False
        if diff.source_type == "clause":
            return all(
                self._clause_evidence_covered(
                    item,
                    relevant,
                    fallback_text=diff.compare_snippet if diff.diff_type == "ADD" else diff.original_snippet,
                )
                for item in evidence
            )
        return all(self._evidence_covered(item, relevant) for item in evidence)

    def _clause_evidence_covered(
        self,
        evidence: EvidenceBox,
        coverage: list[_PageCoverage],
        *,
        fallback_text: str,
    ) -> bool:
        evidence_text = self._content_key(evidence.text or fallback_text)
        return any(
            evidence.page_no == item.page_no
            and self._bbox_contains(item.bbox, evidence.bbox)
            and bool(evidence_text)
            and evidence_text in item.compact_text
            for item in coverage
        )

    def _evidence_covered(self, evidence: EvidenceBox, coverage: list[_PageCoverage]) -> bool:
        return any(
            evidence.page_no == item.page_no and self._bbox_contains(item.bbox, evidence.bbox) for item in coverage
        )

    @staticmethod
    def _coverage(page_diffs: list[DiffItem]) -> list[_PageCoverage]:
        result: list[_PageCoverage] = []
        for diff in page_diffs:
            evidence = diff.compare_evidence if diff.diff_type == "ADD" else diff.original_evidence
            for item in evidence:
                result.append(
                    _PageCoverage(
                        diff_type=diff.diff_type,
                        page_no=item.page_no,
                        bbox=item.bbox,
                        compact_text=PageDiffConsolidator._content_key(
                            diff.compare_text if diff.diff_type == "ADD" else diff.original_text
                        ),
                    )
                )
        return result

    def _is_meaningful_block(self, block: TextBlock, page: Page) -> bool:
        block_type = (block.block_type or "").lower()
        if block_type in self.noise_block_types:
            return False
        if block_type in self.footer_block_types and self._near_page_edge(block, page):
            return False
        text = self._compact(block.text or "")
        if len(text) < 2:
            return False
        if re.fullmatch(r"第?\d+页|共\d+页第\d+页|\d+/\d+", text):
            return False
        return any(char.isalnum() or "\u4e00" <= char <= "\u9fff" for char in text)

    @staticmethod
    def _near_page_edge(block: TextBlock, page: Page) -> bool:
        return bool(page.height and (block.bbox.y0 <= page.height * 0.04 or block.bbox.y1 >= page.height * 0.96))

    @staticmethod
    def _union_bbox(bboxes: list[BBox], page: Page) -> BBox:
        return BBox(
            x0=max(0.0, min(bbox.x0 for bbox in bboxes)),
            y0=max(0.0, min(bbox.y0 for bbox in bboxes)),
            x1=min(page.width, max(bbox.x1 for bbox in bboxes)),
            y1=min(page.height, max(bbox.y1 for bbox in bboxes)),
        )

    @staticmethod
    def _area_ratio(bbox: BBox, page: Page) -> float:
        page_area = max(1.0, page.width * page.height)
        bbox_area = max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)
        return bbox_area / page_area

    def _block_coverage_ratio(self, block_bbox: BBox, evidences: list[EvidenceBox]) -> float:
        block_area = self._bbox_area(block_bbox)
        if block_area <= 0:
            return 0.0
        covered_area = sum(self._intersection_area(block_bbox, evidence.bbox) for evidence in evidences)
        ratio = min(1.0, covered_area / block_area)
        return ratio if ratio >= self.min_block_coverage_ratio else 0.0

    def _block_weight(self, block: TextBlock) -> float:
        area = self._bbox_area(block.bbox)
        text_len = len(self._compact(block.text or ""))
        text_factor = min(4.0, max(1.0, text_len / 40.0))
        return max(1.0, area) * text_factor

    @staticmethod
    def _intersection_area(left: BBox, right: BBox) -> float:
        width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
        height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
        return width * height

    @staticmethod
    def _bbox_area(bbox: BBox) -> float:
        return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)

    @staticmethod
    def _bbox_contains(outer: BBox, inner: BBox, tolerance: float = 2.0) -> bool:
        return (
            inner.x0 >= outer.x0 - tolerance
            and inner.y0 >= outer.y0 - tolerance
            and inner.x1 <= outer.x1 + tolerance
            and inner.y1 <= outer.y1 + tolerance
        )

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    @staticmethod
    def _content_key(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "")
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized, flags=re.UNICODE)

    @staticmethod
    def _next_diff_index(diffs: list[DiffItem]) -> int:
        max_index = 0
        for diff in diffs:
            match = re.fullmatch(r"D(\d+)", diff.diff_id or "")
            if match:
                max_index = max(max_index, int(match.group(1)))
        return max(max_index + 1, len(diffs) + 1)
