from __future__ import annotations

from app.models import BBox, CharBox, Clause, DiffItem, EvidenceBox, TextRange


class EvidenceLocator:
    def locate(
        self,
        diffs: list[DiffItem],
        original_clauses: list[Clause] | None = None,
        compare_clauses: list[Clause] | None = None,
    ) -> list[DiffItem]:
        original_by_id = {clause.clause_id: clause for clause in original_clauses or []}
        compare_by_id = {clause.clause_id: clause for clause in compare_clauses or []}
        for diff in diffs:
            if diff.original_clause_id:
                clause = original_by_id.get(diff.original_clause_id)
                diff.original_evidence = self._locate_clause_ranges(
                    clause,
                    self._side_ranges(diff.original_change_ranges, side="original"),
                    diff.original_snippet,
                    diff.original_evidence,
                    diff.diff_type,
                    side="original",
                )
            if diff.compare_clause_id:
                clause = compare_by_id.get(diff.compare_clause_id)
                diff.compare_evidence = self._locate_clause_ranges(
                    clause,
                    self._side_ranges(diff.compare_change_ranges, side="compare"),
                    diff.compare_snippet,
                    diff.compare_evidence,
                    diff.diff_type,
                    side="compare",
                )
        self._resolve_side_conflicts(diffs, side="original")
        self._resolve_side_conflicts(diffs, side="compare")
        self.assign_evidence_confidence(diffs)
        return diffs

    def _locate_clause_ranges(
        self,
        clause: Clause | None,
        ranges: list[TextRange],
        snippet: str,
        fallback: list[EvidenceBox],
        fallback_type: str,
        side: str,
    ) -> list[EvidenceBox]:
        if clause is None:
            return self._mark_fallback(fallback, snippet, fallback_type, side)
        if not ranges:
            return []

        precise: list[EvidenceBox] = []
        for text_range in ranges:
            precise.extend(self._evidence_for_range(clause, text_range))
        if precise:
            return precise
        return self._mark_fallback(fallback, snippet, fallback_type, side)

    def _evidence_for_range(self, clause: Clause, text_range: TextRange) -> list[EvidenceBox]:
        start = max(0, text_range.start)
        end = min(len(clause.text), text_range.end)
        if start >= end:
            return []
        segments = self._non_whitespace_segments(clause.char_boxes[start:end])
        actual_boxes = [char_box for segment in segments for char_box in segment]
        if not actual_boxes:
            return []
        method = "estimated_char" if any(char_box.text_index is None for char_box in actual_boxes) else "char_exact"
        evidences: list[EvidenceBox] = []
        for segment in segments:
            evidences.extend(
                EvidenceBox(
                    page_no=page_no,
                    bbox=bbox,
                    method=method,
                    text=text,
                    highlight_type=text_range.highlight_type,
                    text_confidence=self._segment_text_confidence(segment),
                )
                for page_no, bbox, text in self._merge_char_boxes(segment)
            )
        return evidences

    def _segment_text_confidence(self, char_boxes: list[CharBox]) -> float | None:
        confidences = [char_box.confidence for char_box in char_boxes if char_box.confidence is not None]
        if not confidences:
            return None
        return min(confidences)

    def _non_whitespace_segments(self, char_boxes: list[CharBox | None]) -> list[list[CharBox]]:
        segments: list[list[CharBox]] = []
        current: list[CharBox] = []
        for char_box in char_boxes:
            if char_box is None or not char_box.char.strip():
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append(char_box)
        if current:
            segments.append(current)
        return segments

    def _merge_char_boxes(self, char_boxes: list[CharBox]) -> list[tuple[int, BBox, str]]:
        merged: list[tuple[int, BBox, str]] = []
        current_page = char_boxes[0].page_no
        current_bbox = char_boxes[0].bbox
        current_text = char_boxes[0].char
        current_mid_y = self._mid_y(current_bbox)
        current_height = max(1.0, current_bbox.y1 - current_bbox.y0)

        for char_box in char_boxes[1:]:
            bbox = char_box.bbox
            same_page = char_box.page_no == current_page
            same_line = abs(self._mid_y(bbox) - current_mid_y) <= max(3.0, current_height * 0.65)
            gap = bbox.x0 - current_bbox.x1
            same_run = gap <= max(2.0, current_height * 0.35)
            if same_page and same_line and same_run:
                current_bbox = self._union_rectified(current_bbox, bbox, current_height)
                current_text += char_box.char
                current_mid_y = self._mid_y(current_bbox)
                current_height = max(current_height, current_bbox.y1 - current_bbox.y0)
                continue
            merged.append((current_page, self._pad(current_bbox), current_text))
            current_page = char_box.page_no
            current_bbox = bbox
            current_text = char_box.char
            current_mid_y = self._mid_y(current_bbox)
            current_height = max(1.0, current_bbox.y1 - current_bbox.y0)
        merged.append((current_page, self._pad(current_bbox), current_text))
        return merged

    def _side_ranges(self, ranges: list[TextRange], side: str) -> list[TextRange]:
        if side == "original":
            return [text_range for text_range in ranges if text_range.highlight_type in {"DELETE", "MODIFY"}]
        return [text_range for text_range in ranges if text_range.highlight_type in {"ADD", "MODIFY"}]

    def _mark_fallback(
        self,
        evidences: list[EvidenceBox],
        snippet: str,
        highlight_type: str,
        side: str,
    ) -> list[EvidenceBox]:
        if not self._is_allowed_on_side(highlight_type, side):
            return []
        for evidence in evidences:
            evidence.highlight_type = evidence.highlight_type or highlight_type
            if not self._is_allowed_on_side(evidence.highlight_type, side):
                continue
            if snippet and snippet in evidence.text:
                evidence.method = "exact_text"
            elif evidence.method == "block_fallback":
                evidence.method = "block_fallback"
            else:
                evidence.method = "clause_fallback"
        return [evidence for evidence in evidences if self._is_allowed_on_side(evidence.highlight_type, side)]

    def _is_allowed_on_side(self, highlight_type: str | None, side: str) -> bool:
        if side == "original":
            return highlight_type in {"DELETE", "MODIFY"}
        return highlight_type in {"ADD", "MODIFY"}

    def _resolve_side_conflicts(self, diffs: list[DiffItem], side: str) -> None:
        items: list[tuple[int, int, EvidenceBox]] = []
        for diff_index, diff in enumerate(diffs):
            evidences = diff.original_evidence if side == "original" else diff.compare_evidence
            for evidence_index, evidence in enumerate(evidences):
                if self._is_allowed_on_side(evidence.highlight_type, side):
                    items.append((diff_index, evidence_index, evidence))

        discarded: set[tuple[int, int]] = set()
        for left_pos, (left_diff, left_index, left) in enumerate(items):
            if (left_diff, left_index) in discarded:
                continue
            for right_diff, right_index, right in items[left_pos + 1 :]:
                if (right_diff, right_index) in discarded:
                    continue
                if not self._evidence_conflicts(left, right):
                    continue
                winner = self._preferred_evidence(left, right, side)
                if winner is left:
                    discarded.add((right_diff, right_index))
                else:
                    discarded.add((left_diff, left_index))
                    break

        for diff_index, diff in enumerate(diffs):
            evidences = diff.original_evidence if side == "original" else diff.compare_evidence
            kept = [evidence for index, evidence in enumerate(evidences) if (diff_index, index) not in discarded]
            if side == "original":
                diff.original_evidence = kept
            else:
                diff.compare_evidence = kept

    def _evidence_conflicts(self, left: EvidenceBox, right: EvidenceBox) -> bool:
        if left.page_no != right.page_no:
            return False
        coverage = self._smaller_coverage(left.bbox, right.bbox)
        if coverage < 0.7:
            return False
        left_text = (left.text or "").strip()
        right_text = (right.text or "").strip()
        return not left_text or not right_text or left_text in right_text or right_text in left_text

    def _preferred_evidence(self, left: EvidenceBox, right: EvidenceBox, side: str) -> EvidenceBox:
        left_priority = self._highlight_priority(left.highlight_type, side)
        right_priority = self._highlight_priority(right.highlight_type, side)
        if left_priority != right_priority:
            return left if left_priority > right_priority else right
        left_area = self._area(left.bbox)
        right_area = self._area(right.bbox)
        return left if left_area <= right_area else right

    def _highlight_priority(self, highlight_type: str | None, side: str) -> int:
        if side == "compare":
            return {"ADD": 3, "MODIFY": 2}.get(highlight_type or "", 0)
        return {"DELETE": 3, "MODIFY": 2}.get(highlight_type or "", 0)

    def _smaller_coverage(self, left: BBox, right: BBox) -> float:
        intersection = self._intersection_area(left, right)
        smaller = min(self._area(left), self._area(right))
        if smaller <= 0:
            return 0.0
        return intersection / smaller

    def _intersection_area(self, left: BBox, right: BBox) -> float:
        width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
        height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
        return width * height

    def _area(self, bbox: BBox) -> float:
        return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)

    def _union(self, left: BBox, right: BBox) -> BBox:
        return BBox(
            x0=min(left.x0, right.x0),
            y0=min(left.y0, right.y0),
            x1=max(left.x1, right.x1),
            y1=max(left.y1, right.y1),
        )

    def _union_rectified(self, left: BBox, right: BBox, reference_height: float) -> BBox:
        merged_x0 = min(left.x0, right.x0)
        merged_x1 = max(left.x1, right.x1)
        mid_y = (self._mid_y(left) + self._mid_y(right)) / 2
        max_height = max(reference_height, left.y1 - left.y0, right.y1 - right.y0)
        return BBox(x0=merged_x0, y0=mid_y - max_height / 2, x1=merged_x1, y1=mid_y + max_height / 2)

    def _pad(self, bbox: BBox) -> BBox:
        return BBox(x0=max(0.0, bbox.x0 - 0.8), y0=max(0.0, bbox.y0 - 0.8), x1=bbox.x1 + 0.8, y1=bbox.y1 + 0.8)

    def _mid_y(self, bbox: BBox) -> float:
        return (bbox.y0 + bbox.y1) / 2

    def assign_evidence_confidence(self, diffs: list[DiffItem]) -> None:
        for diff in diffs:
            for evidence in [*diff.original_evidence, *diff.compare_evidence]:
                confidence = self._confidence_for_method(evidence.method)
                if evidence.method == "table_cell" and self._has_possible_ocr_fragment_flag(diff):
                    confidence = min(confidence, 0.55)
                evidence.confidence = confidence
                evidence.evidence_quality = self._quality_for_confidence(confidence)

    @staticmethod
    def _has_possible_ocr_fragment_flag(diff: DiffItem) -> bool:
        return any(flag.lower() == "possible_ocr_fragment" for flag in diff.review_flags)

    def _confidence_for_method(self, method: str) -> float:
        method = (method or "").lower()
        if method in {"char_exact", "text_exact"}:
            return 0.98
        if method in {"table_cell", "cover_metadata"}:
            return 0.9
        if method == "estimated_char":
            return 0.74
        if method in {"exact_text", "cover_extra", "clause_fallback"}:
            return 0.68
        if method == "block_fallback":
            return 0.46
        return 0.55

    def _quality_for_confidence(self, confidence: float) -> str:
        if confidence >= 0.85:
            return "HIGH"
        if confidence >= 0.6:
            return "MEDIUM"
        return "LOW"
