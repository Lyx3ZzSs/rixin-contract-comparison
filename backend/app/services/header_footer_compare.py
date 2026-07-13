from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata
from dataclasses import dataclass

from app.models import (
    BBox,
    DiffItem,
    DiffType,
    Document,
    EvidenceBox,
    TextBlock,
    TextRange,
)
from app.services.diff_engine import DiffEngine
from app.utils.id_utils import generate_diff_id


@dataclass(frozen=True)
class HeaderFooterCandidate:
    slot: str
    text: str
    page_no: int
    bbox: BBox
    block_id: str
    is_page_number: bool = False
    explicit: bool = False
    is_lower_footer_annotation: bool = False


@dataclass(frozen=True)
class HeaderFooterEntry:
    slot: str
    text: str
    key: str
    evidences: list[EvidenceBox]
    is_page_number: bool = False


@dataclass(frozen=True)
class HeaderFooterFuzzyMatch:
    left_index: int
    right_index: int
    score: float
    text_score: float
    position_score: float
    page_overlap_score: float
    supplemental: bool = False


class HeaderFooterComparator:
    """Builds audit diffs for document headers and footers."""

    header_types = {"header", "page_header"}
    footer_types = {"footer", "page_footer"}
    footnote_types = {"footnote", "vision_footnote"}
    excluded_types = {
        "table",
        "table_title",
        "table_cell",
        "image",
        "figure",
        "seal",
        "chart",
        "formula",
        "vertical_text",
        "watermark",
    }
    page_number_pattern = re.compile(r"^(?:第?\s*\d+\s*页?|共\s*\d+\s*页\s*第\s*\d+\s*页)$")
    page_number_with_total_pattern = re.compile(r"^共\s*(?P<total>\d+)\s*页\s*第\s*(?P<page>\d+)\s*页$")
    fuzzy_match_flag = "FUZZY_HEADER_FOOTER_MATCH"
    footer_annotation_band = 0.90
    footer_variant_prototype_min_pages = 3
    footer_variant_max_characters = 8

    def build_diffs(self, original: Document, compare: Document, start_index: int = 1) -> list[DiffItem]:
        original_entries = self._entries_by_slot(original)
        compare_entries = self._entries_by_slot(compare)
        diffs: list[DiffItem] = []
        next_index = start_index

        for slot in ("header", "footer"):
            slot_diffs = self._diff_slot(
                original_entries.get(slot, []),
                compare_entries.get(slot, []),
                next_index,
            )
            diffs.extend(slot_diffs)
            next_index += len(slot_diffs)
        return diffs

    def _entries_by_slot(self, document: Document) -> dict[str, list[HeaderFooterEntry]]:
        candidates_by_slot: dict[str, list[HeaderFooterCandidate]] = {"header": [], "footer": []}
        for page in document.pages:
            for block in page.blocks:
                candidate = self._candidate(block, page.height)
                if candidate is None:
                    continue
                candidates_by_slot[candidate.slot].append(candidate)

        entries: dict[str, list[HeaderFooterEntry]] = {}
        for slot, candidates in candidates_by_slot.items():
            slot_entries = self._non_page_number_entries(slot, candidates)
            page_number_entry = self._page_number_entry(slot, candidates)
            if page_number_entry is not None:
                slot_entries.append(page_number_entry)
            if slot_entries:
                entries[slot] = slot_entries
        return entries

    def _candidate(self, block: TextBlock, page_height: float) -> HeaderFooterCandidate | None:
        text = self._clean_text(block.text)
        if not text:
            return None

        block_type = (block.block_type or "").lower()
        if block_type in self.excluded_types:
            return None
        is_page_number = self._is_page_number(text)
        explicit_header = block_type in self.header_types
        explicit_footer = block_type in self.footer_types
        lower_footer_annotation = (
            page_height > 0 and block.bbox.y0 >= page_height * self.footer_annotation_band
        )
        if (block.layout_match_status or "") == "noise_unmatched" and not (
            explicit_footer and lower_footer_annotation
        ):
            return None
        if self._is_bare_field_label(text):
            return None
        if self._looks_like_short_edge_noise(text, block, page_height):
            return None
        if is_page_number and block.bbox.y1 <= page_height * 0.12:
            return None
        slot = ""
        if explicit_header:
            slot = "header"
        elif explicit_footer and not self._looks_like_clause_start(text):
            slot = "footer"
        elif page_height > 0:
            near_top = block.bbox.y1 <= page_height * 0.08
            near_bottom = block.bbox.y0 >= page_height * 0.92
            near_page_number_bottom = is_page_number and block.bbox.y0 >= page_height * 0.90
            if near_top and not self._looks_like_clause_start(text):
                slot = "header"
            elif near_bottom or near_page_number_bottom:
                slot = "footer"
            # A repeated exact-text group is required before this non-explicit
            # annotation is emitted, keeping one-off lower body text excluded.
            elif lower_footer_annotation and not self._looks_like_clause_start(text):
                slot = "footer"
        if not slot:
            return None

        return HeaderFooterCandidate(
            slot=slot,
            text=text,
            page_no=block.page_no,
            bbox=block.bbox,
            block_id=block.block_id,
            is_page_number=is_page_number,
            explicit=explicit_header or explicit_footer,
            is_lower_footer_annotation=slot == "footer" and lower_footer_annotation,
        )

    def _non_page_number_entries(
        self,
        slot: str,
        candidates: list[HeaderFooterCandidate],
    ) -> list[HeaderFooterEntry]:
        grouped: dict[str, list[HeaderFooterCandidate]] = {}
        for candidate in candidates:
            if candidate.is_page_number:
                continue
            key = self._compare_key(candidate.text)
            if key:
                grouped.setdefault(key, []).append(candidate)

        if slot == "footer":
            self._merge_repeated_footer_annotation_variants(grouped)

        entries: list[HeaderFooterEntry] = []
        for key, group in sorted(grouped.items(), key=lambda item: self._entry_sort_key(item[1])):
            if (
                not any(candidate.explicit for candidate in group)
                and len({candidate.page_no for candidate in group}) < 2
            ):
                continue
            sample = group[0]
            entries.append(
                HeaderFooterEntry(
                    slot=slot,
                    text=sample.text,
                    key=key,
                    evidences=self._evidences(group),
                )
            )
        return entries

    def _merge_repeated_footer_annotation_variants(
        self,
        grouped: dict[str, list[HeaderFooterCandidate]],
    ) -> None:
        prototypes = [
            (key, group)
            for key, group in grouped.items()
            if self._is_footer_annotation_prototype(group)
        ]
        for variant_key, variant_group in list(grouped.items()):
            if len(variant_group) != 1 or variant_key not in grouped:
                continue
            variant = variant_group[0]
            if not self._is_single_page_footer_annotation_variant(variant):
                continue
            matches = [
                (key, group)
                for key, group in prototypes
                if key != variant_key and self._matches_footer_annotation_prototype(variant, key, group)
            ]
            if len(matches) != 1:
                continue
            prototype_key, prototype_group = matches[0]
            prototype_group.append(variant)
            del grouped[variant_key]

    def _is_footer_annotation_prototype(self, group: list[HeaderFooterCandidate]) -> bool:
        return (
            len({candidate.page_no for candidate in group}) >= self.footer_variant_prototype_min_pages
            and all(candidate.is_lower_footer_annotation for candidate in group)
        )

    @staticmethod
    def _is_single_page_footer_annotation_variant(candidate: HeaderFooterCandidate) -> bool:
        return not candidate.explicit and candidate.is_lower_footer_annotation

    def _matches_footer_annotation_prototype(
        self,
        variant: HeaderFooterCandidate,
        prototype_key: str,
        prototype_group: list[HeaderFooterCandidate],
    ) -> bool:
        variant_key = self._compare_key(variant.text)
        if not self._is_short_cjk_annotation(prototype_key) or not self._is_short_cjk_annotation(variant_key):
            return False
        prototype_pages = {candidate.page_no for candidate in prototype_group}
        if variant.page_no in prototype_pages:
            return False
        if not self._footer_annotation_position_matches(variant, prototype_group):
            return False
        if prototype_key in variant_key or variant_key in prototype_key:
            return True
        return (
            len(prototype_key) == len(variant_key)
            and len(prototype_key) >= 2
            and prototype_key[-1] == variant_key[-1]
            and sum(left != right for left, right in zip(prototype_key, variant_key, strict=True)) == 1
        )

    def _is_short_cjk_annotation(self, text: str) -> bool:
        return 2 <= len(text) <= self.footer_variant_max_characters and all("\u4e00" <= char <= "\u9fff" for char in text)

    def _footer_annotation_position_matches(
        self,
        variant: HeaderFooterCandidate,
        prototype_group: list[HeaderFooterCandidate],
    ) -> bool:
        variant_center_x = (variant.bbox.x0 + variant.bbox.x1) / 2
        variant_center_y = (variant.bbox.y0 + variant.bbox.y1) / 2
        prototype_centers = [
            ((candidate.bbox.x0 + candidate.bbox.x1) / 2, (candidate.bbox.y0 + candidate.bbox.y1) / 2)
            for candidate in prototype_group
        ]
        return any(
            abs(variant_center_x - center_x) <= 120 and abs(variant_center_y - center_y) <= 48
            for center_x, center_y in prototype_centers
        )

    def _page_number_entry(
        self,
        slot: str,
        candidates: list[HeaderFooterCandidate],
    ) -> HeaderFooterEntry | None:
        page_numbers = [candidate for candidate in candidates if candidate.is_page_number]
        if not page_numbers:
            return None
        profile = self._page_number_profile(page_numbers)
        return HeaderFooterEntry(
            slot=slot,
            text=self._page_number_summary(page_numbers, profile),
            key=f"page_number:{profile}",
            evidences=self._evidences(page_numbers),
            is_page_number=True,
        )

    def _diff_slot(
        self,
        original: list[HeaderFooterEntry],
        compare: list[HeaderFooterEntry],
        start_index: int,
    ) -> list[DiffItem]:
        diffs: list[DiffItem] = []
        next_index = start_index
        original_regular = [entry for entry in original if not entry.is_page_number]
        compare_regular = [entry for entry in compare if not entry.is_page_number]

        matched_original: set[str] = set()
        matched_compare: set[str] = set()
        for left in original_regular:
            for right in compare_regular:
                if right.key == left.key:
                    matched_original.add(left.key)
                    matched_compare.add(right.key)
                    break

        unmatched_original = [entry for entry in original_regular if entry.key not in matched_original]
        unmatched_compare = [entry for entry in compare_regular if entry.key not in matched_compare]
        unmatched_original_indexes = {
            index for index, entry in enumerate(original_regular) if entry.key not in matched_original
        }
        unmatched_compare_indexes = {
            index for index, entry in enumerate(compare_regular) if entry.key not in matched_compare
        }

        fuzzy_matches = self._fuzzy_matches(
            original_regular,
            compare_regular,
            unmatched_original,
            unmatched_compare,
        )
        for match in fuzzy_matches:
            left = original_regular[match.left_index]
            right = compare_regular[match.right_index]
            aligned_left, aligned_right = self._aligned_fuzzy_entries(left, right)
            diffs.append(
                self._modify_diff(
                    aligned_left,
                    aligned_right,
                    next_index,
                    match_method="fuzzy_position_header_footer",
                    match_score=round(match.score, 2),
                    match_score_details={
                        "text_score": round(match.text_score, 2),
                        "position_score": round(match.position_score, 2),
                        "page_overlap_score": round(match.page_overlap_score, 2),
                    },
                    review_flags=[self.fuzzy_match_flag],
                )
            )
            next_index += 1

        fuzzy_original_keys = {
            match.left_index for match in fuzzy_matches if match.left_index in unmatched_original_indexes
        }
        fuzzy_compare_keys = {
            match.right_index for match in fuzzy_matches if match.right_index in unmatched_compare_indexes
        }
        unmatched_original = [
            entry
            for index, entry in enumerate(original_regular)
            if index in unmatched_original_indexes - fuzzy_original_keys
        ]
        unmatched_compare = [
            entry
            for index, entry in enumerate(compare_regular)
            if index in unmatched_compare_indexes - fuzzy_compare_keys
        ]

        if (
            len(unmatched_original) == 1
            and len(unmatched_compare) == 1
            and self._can_fallback_modify(unmatched_original[0], unmatched_compare[0])
        ):
            diffs.append(self._modify_diff(unmatched_original[0], unmatched_compare[0], next_index))
            next_index += 1
        else:
            for entry in unmatched_original:
                diffs.append(self._one_sided_diff(entry, "DELETE", next_index))
                next_index += 1
            for entry in unmatched_compare:
                diffs.append(self._one_sided_diff(entry, "ADD", next_index))
                next_index += 1

        original_page_number = next((entry for entry in original if entry.is_page_number), None)
        compare_page_number = next((entry for entry in compare if entry.is_page_number), None)
        if original_page_number is not None or compare_page_number is not None:
            page_diff = self._page_number_diff(original_page_number, compare_page_number, next_index)
            if page_diff is not None:
                diffs.append(page_diff)

        return diffs

    def _page_number_diff(
        self,
        left: HeaderFooterEntry | None,
        right: HeaderFooterEntry | None,
        index: int,
    ) -> DiffItem | None:
        if left is not None and right is not None and left.key == right.key:
            return None
        if left is not None and right is not None:
            return self._modify_diff(left, right, index)
        if right is not None:
            return self._one_sided_diff(right, "ADD", index)
        assert left is not None
        return self._one_sided_diff(left, "DELETE", index)

    def _fuzzy_matches(
        self,
        original: list[HeaderFooterEntry],
        compare: list[HeaderFooterEntry],
        unmatched_original: list[HeaderFooterEntry],
        unmatched_compare: list[HeaderFooterEntry],
    ) -> list[HeaderFooterFuzzyMatch]:
        original_indexes = {id(entry): index for index, entry in enumerate(original)}
        compare_indexes = {id(entry): index for index, entry in enumerate(compare)}
        candidates: list[HeaderFooterFuzzyMatch] = []

        unmatched_original_indexes = [original_indexes[id(entry)] for entry in unmatched_original]
        unmatched_compare_indexes = [compare_indexes[id(entry)] for entry in unmatched_compare]
        for left_index in unmatched_original_indexes:
            for right_index in unmatched_compare_indexes:
                match = self._score_fuzzy_match(original[left_index], compare[right_index], left_index, right_index)
                if match is not None:
                    candidates.append(match)

        # A one-page OCR variant can be left unmatched while the corresponding
        # right-side repeated header was already consumed by an exact match.
        for left_index in unmatched_original_indexes:
            for right_index, right in enumerate(compare):
                if right_index in unmatched_compare_indexes:
                    continue
                match = self._score_fuzzy_match(
                    original[left_index],
                    right,
                    left_index,
                    right_index,
                    supplemental=True,
                )
                if match is not None:
                    candidates.append(match)

        for right_index in unmatched_compare_indexes:
            for left_index, left in enumerate(original):
                if left_index in unmatched_original_indexes:
                    continue
                match = self._score_fuzzy_match(
                    left,
                    compare[right_index],
                    left_index,
                    right_index,
                    supplemental=True,
                )
                if match is not None:
                    candidates.append(match)

        matches: list[HeaderFooterFuzzyMatch] = []
        used_original: set[int] = set()
        used_unmatched_compare: set[int] = set()
        unmatched_compare_index_set = set(unmatched_compare_indexes)
        for match in sorted(candidates, key=lambda item: item.score, reverse=True):
            if match.left_index in used_original:
                continue
            if match.right_index in unmatched_compare_index_set and match.right_index in used_unmatched_compare:
                continue
            matches.append(match)
            used_original.add(match.left_index)
            if match.right_index in unmatched_compare_index_set:
                used_unmatched_compare.add(match.right_index)
        return matches

    def _score_fuzzy_match(
        self,
        left: HeaderFooterEntry,
        right: HeaderFooterEntry,
        left_index: int,
        right_index: int,
        *,
        supplemental: bool = False,
    ) -> HeaderFooterFuzzyMatch | None:
        if left.slot != right.slot:
            return None
        if not self._is_fuzzy_matchable(left) or not self._is_fuzzy_matchable(right):
            return None

        text_score = self._text_similarity(left.key, right.key)
        if text_score < 88.0:
            return None

        position_score = self._position_similarity(left.evidences, right.evidences)
        page_overlap_score = self._page_overlap_score(left.evidences, right.evidences)
        score = text_score * 0.62 + position_score * 0.28 + page_overlap_score * 0.10
        strict_match = text_score >= 88.0 and position_score >= 70.0 and page_overlap_score >= 40.0 and score >= 82.0
        high_text_match = text_score >= 94.0 and position_score >= 85.0 and score >= 88.0
        if not strict_match and not high_text_match:
            return None

        return HeaderFooterFuzzyMatch(
            left_index=left_index,
            right_index=right_index,
            score=score,
            text_score=text_score,
            position_score=position_score,
            page_overlap_score=page_overlap_score,
            supplemental=supplemental,
        )

    def _is_fuzzy_matchable(self, entry: HeaderFooterEntry) -> bool:
        if entry.is_page_number:
            return False
        return len(entry.key) >= 4

    def _can_fallback_modify(self, left: HeaderFooterEntry, right: HeaderFooterEntry) -> bool:
        return self._is_fuzzy_matchable(left) or self._is_fuzzy_matchable(right)

    def _text_similarity(self, left: str, right: str) -> float:
        return SequenceMatcher(None, left, right).ratio() * 100.0

    def _page_overlap_score(self, left: list[EvidenceBox], right: list[EvidenceBox]) -> float:
        left_pages = {evidence.page_no for evidence in left}
        right_pages = {evidence.page_no for evidence in right}
        if not left_pages or not right_pages:
            return 0.0
        overlap = left_pages & right_pages
        if not overlap:
            return 0.0
        return len(overlap) / min(len(left_pages), len(right_pages)) * 100.0

    def _position_similarity(self, left: list[EvidenceBox], right: list[EvidenceBox]) -> float:
        left_by_page = {evidence.page_no: evidence for evidence in left}
        right_by_page = {evidence.page_no: evidence for evidence in right}
        common_pages = sorted(set(left_by_page) & set(right_by_page))
        if not common_pages:
            return 0.0
        scores = [
            self._bbox_similarity(left_by_page[page_no].bbox, right_by_page[page_no].bbox) for page_no in common_pages
        ]
        return max(scores)

    def _bbox_similarity(self, left: BBox, right: BBox) -> float:
        left_width = max(1.0, left.x1 - left.x0)
        right_width = max(1.0, right.x1 - right.x0)
        left_height = max(1.0, left.y1 - left.y0)
        right_height = max(1.0, right.y1 - right.y0)
        left_center_x = (left.x0 + left.x1) / 2
        right_center_x = (right.x0 + right.x1) / 2
        left_center_y = (left.y0 + left.y1) / 2
        right_center_y = (right.y0 + right.y1) / 2

        y_score = self._distance_score(abs(left_center_y - right_center_y), 40.0)
        x_score = self._distance_score(abs(left_center_x - right_center_x), 120.0)
        height_score = self._distance_score(abs(left_height - right_height), 40.0)
        width_score = self._distance_score(abs(left_width - right_width), 180.0)
        return y_score * 0.45 + x_score * 0.25 + height_score * 0.15 + width_score * 0.15

    def _distance_score(self, distance: float, tolerance: float) -> float:
        return max(0.0, 100.0 - min(distance / tolerance, 1.0) * 100.0)

    def _aligned_fuzzy_entries(
        self,
        left: HeaderFooterEntry,
        right: HeaderFooterEntry,
    ) -> tuple[HeaderFooterEntry, HeaderFooterEntry]:
        left_by_page = {evidence.page_no: evidence for evidence in left.evidences}
        right_by_page = {evidence.page_no: evidence for evidence in right.evidences}
        common_pages = sorted(set(left_by_page) & set(right_by_page))
        if not common_pages:
            return left, right
        return (
            self._entry_with_evidences(left, [left_by_page[page_no] for page_no in common_pages]),
            self._entry_with_evidences(right, [right_by_page[page_no] for page_no in common_pages]),
        )

    def _entry_with_evidences(
        self,
        entry: HeaderFooterEntry,
        evidences: list[EvidenceBox],
    ) -> HeaderFooterEntry:
        return HeaderFooterEntry(
            slot=entry.slot,
            text=entry.text,
            key=entry.key,
            evidences=evidences,
            is_page_number=entry.is_page_number,
        )

    def _modify_diff(
        self,
        left: HeaderFooterEntry,
        right: HeaderFooterEntry,
        index: int,
        *,
        match_method: str = "",
        match_score: float | None = None,
        match_score_details: dict[str, float] | None = None,
        review_flags: list[str] | None = None,
    ) -> DiffItem:
        original_snippet, compare_snippet, original_ranges, compare_ranges = DiffEngine()._changed_snippets(
            left.text,
            right.text,
        )
        if not original_ranges:
            original_ranges = [TextRange(start=0, end=len(left.text), highlight_type="MODIFY")]
        if not compare_ranges:
            compare_ranges = [TextRange(start=0, end=len(right.text), highlight_type="MODIFY")]
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="MODIFY",
            title=self._title(left),
            original_text=left.text,
            compare_text=right.text,
            original_snippet=original_snippet or left.text,
            compare_snippet=compare_snippet or right.text,
            readable_change=f"{self._slot_label(left.slot)}变更：{left.text} -> {right.text}",
            source_type="header_footer",
            match_score=match_score,
            match_method=match_method,
            match_score_details=match_score_details or {},
            review_flags=review_flags or [],
            original_evidence=self._mark_evidences(left.evidences, "MODIFY"),
            compare_evidence=self._mark_evidences(right.evidences, "MODIFY"),
            original_change_ranges=original_ranges,
            compare_change_ranges=compare_ranges,
        )

    def _one_sided_diff(self, entry: HeaderFooterEntry, diff_type: DiffType, index: int) -> DiffItem:
        is_add = diff_type == "ADD"
        text_range = TextRange(start=0, end=len(entry.text), highlight_type=diff_type)
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=diff_type,
            title=self._title(entry),
            original_text="" if is_add else entry.text,
            compare_text=entry.text if is_add else "",
            original_snippet="" if is_add else entry.text,
            compare_snippet=entry.text if is_add else "",
            readable_change=f"{'新增' if is_add else '删除'}{self._slot_label(entry.slot)}：{entry.text}",
            source_type="header_footer",
            original_evidence=[] if is_add else self._mark_evidences(entry.evidences, diff_type),
            compare_evidence=self._mark_evidences(entry.evidences, diff_type) if is_add else [],
            original_change_ranges=[] if is_add else [text_range],
            compare_change_ranges=[text_range] if is_add else [],
        )

    def _title(self, entry: HeaderFooterEntry) -> str:
        label = self._slot_label(entry.slot)
        if entry.is_page_number:
            return f"{label}页码"
        return label

    def _slot_label(self, slot: str) -> str:
        return "页眉" if slot == "header" else "页脚"

    def _evidences(self, candidates: list[HeaderFooterCandidate]) -> list[EvidenceBox]:
        evidences: list[EvidenceBox] = []
        seen_pages: set[int] = set()
        for candidate in sorted(candidates, key=lambda item: (item.page_no, item.bbox.y0, item.bbox.x0)):
            if candidate.page_no in seen_pages:
                continue
            evidences.append(
                EvidenceBox(
                    page_no=candidate.page_no,
                    bbox=candidate.bbox,
                    method="header_footer",
                    text=candidate.text,
                    confidence=0.8,
                )
            )
            seen_pages.add(candidate.page_no)
        return evidences

    def _mark_evidences(self, evidences: list[EvidenceBox], diff_type: DiffType) -> list[EvidenceBox]:
        return [evidence.model_copy(update={"highlight_type": diff_type}) for evidence in evidences]

    def _page_number_profile(self, candidates: list[HeaderFooterCandidate]) -> str:
        totals: set[str] = set()
        has_total_style = False
        for candidate in candidates:
            compact = self._compact(candidate.text)
            match = self.page_number_with_total_pattern.fullmatch(compact)
            if match:
                has_total_style = True
                totals.add(match.group("total"))
        if has_total_style:
            return f"total:{','.join(sorted(totals))}"
        return "simple"

    def _page_number_summary(self, candidates: list[HeaderFooterCandidate], profile: str) -> str:
        pages = sorted({candidate.page_no for candidate in candidates})
        page_text = f"覆盖第 {pages[0]} 页" if len(pages) == 1 else f"覆盖第 {pages[0]}-{pages[-1]} 页"
        if profile.startswith("total:"):
            total = profile.removeprefix("total:") or "?"
            return f"页码格式：共 {total} 页第 N 页（{page_text}）"
        return f"页码格式：第 N 页（{page_text}）"

    def _entry_sort_key(self, candidates: list[HeaderFooterCandidate]) -> tuple[int, float, float, str]:
        first = sorted(candidates, key=lambda item: (item.page_no, item.bbox.y0, item.bbox.x0, item.block_id))[0]
        return first.page_no, first.bbox.y0, first.bbox.x0, first.text

    def _clean_text(self, text: str) -> str:
        value = (text or "").replace("\r", "\n")
        lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line).strip()

    def _compare_key(self, text: str) -> str:
        compact = self._compact(text)
        return re.sub(r"[，。；：、“”‘’（）()\[\]【】《》,.!?:;\"']+", "", compact).lower()

    def _compact(self, text: str) -> str:
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))

    def _is_page_number(self, text: str) -> bool:
        return bool(self.page_number_pattern.fullmatch(self._compact(text)))

    def _looks_like_clause_start(self, text: str) -> bool:
        compact = self._compact(text)
        return bool(
            re.match(r"^第[一二三四五六七八九十百千万0-9]+[章节条]", compact)
            or re.match(r"^[一二三四五六七八九十]+、", compact)
            or re.match(r"^\d+(?:\.\d+){0,3}[.、]", compact)
        )

    def _is_bare_field_label(self, text: str) -> bool:
        compact = self._compact(text).rstrip(":：")
        return compact in {"合同编号", "项目名称", "签订时间", "签订日期", "签订地点", "有效期限", "有效期"}

    def _looks_like_short_edge_noise(self, text: str, block: TextBlock, page_height: float) -> bool:
        compact = self._compact(text)
        if not (1 <= len(compact) <= 2):
            return False
        if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]+", compact):
            return False
        near_top = block.bbox.y1 <= page_height * 0.12 or block.bbox.y0 <= page_height * 0.05
        near_left_edge = block.bbox.x0 <= 100
        near_logo_band = near_left_edge and block.bbox.y0 <= page_height * 0.15
        near_page_number_band = bool(re.fullmatch(r"\d{1,2}", compact)) and block.bbox.y1 <= page_height * 0.14
        return (near_top and (near_left_edge or near_page_number_band)) or near_logo_band
