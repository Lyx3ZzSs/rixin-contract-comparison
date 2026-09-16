from __future__ import annotations

from difflib import SequenceMatcher
import re
from statistics import median
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
    is_handwritten_annotation: bool = False
    confidence: float | None = None


@dataclass(frozen=True)
class HeaderFooterEntry:
    slot: str
    text: str
    key: str
    evidences: list[EvidenceBox]
    is_page_number: bool = False
    is_handwritten_annotation: bool = False


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
    page_number_footer_band = 0.88
    footer_variant_prototype_min_pages = 5
    footer_variant_max_characters = 8
    footer_variant_substitution_max_confidence = 0.85
    footer_annotation_cluster_max_x_delta = 72
    footer_annotation_cluster_max_y_delta = 36

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
        from app.services.recognition_evidence import retain_recognition_evidence
        retain_recognition_evidence(diffs, original, compare)
        return diffs

    def owned_footer_blocks(self, document: Document) -> set[tuple[int, str]]:
        """Route confirmed footer evidence away from the body comparison.

        Use the same document-level repetition decision as the footer output,
        and match physical evidence rather than deleting equal body strings.
        One-off lower body lines and signing fields remain untouched.
        """
        evidences = [e for entry in self._entries_by_slot(document).get("footer", [])
                     for e in entry.evidences]
        return {
            (page.page_no, block.block_id)
            for page in document.pages for block in page.blocks
            if page.height > 0 and block.bbox.y0 >= page.height * 0.85
            and len(self._compact(block.text)) <= self.footer_variant_max_characters
            and any(e.page_no == page.page_no and e.bbox == block.bbox for e in evidences)
        }

    def _entries_by_slot(self, document: Document) -> dict[str, list[HeaderFooterEntry]]:
        # A scan canvas may extend below the printed page (watermark/padding).
        # Trust a consecutive, explicitly classified page-number sequence there.
        sequence = []
        for page in document.pages:
            numbers = [b for b in page.blocks if b.block_type == "number"
                       and re.fullmatch(r"\d+", self._compact(b.text))
                       and page.height > 0 and b.bbox.y0 >= page.height * 0.75]
            if len(numbers) == 1:
                sequence.append((page, numbers[0]))
        trusted_numbers: set[str] = set()
        for (lp, lb), (rp, rb) in zip(sequence, sequence[1:]):
            if (rp.page_no == lp.page_no + 1
                    and int(self._compact(rb.text)) == int(self._compact(lb.text)) + 1
                    and abs(lb.bbox.y0 / lp.height - rb.bbox.y0 / rp.height) <= 0.04):
                trusted_numbers.update((lb.block_id, rb.block_id))
        candidates_by_slot: dict[str, list[HeaderFooterCandidate]] = {"header": [], "footer": []}
        for page in document.pages:
            for block in page.blocks:
                candidate = self._candidate(block, page.height, trusted_page_number=block.block_id in trusted_numbers)
                if candidate is None:
                    continue
                candidates_by_slot[candidate.slot].append(candidate)

        # The scan canvas can shift a repeated handwritten footer just above
        # the fixed 90% band. Recall it from its physical multi-page column,
        # never from its characters alone. Keep changed handwriting as its own
        # footer entry; this is role assignment, not OCR text correction.
        footer_candidates = candidates_by_slot["footer"]
        prototypes = [c for c in footer_candidates
                      if c.is_lower_footer_annotation and not c.is_page_number
                      and len(self._compact(c.text)) <= self.footer_variant_max_characters]
        selected = {(c.page_no, c.block_id) for c in footer_candidates}
        body_height_ratios = [(b.bbox.y1-b.bbox.y0)/p.height
                              for p in document.pages if p.height > 0 for b in p.blocks
                              if b.block_type == "text" and len(self._compact(b.text)) >= 20
                              and b.bbox.y1 < p.height * 0.85]
        for page in document.pages:
            if page.width <= 0 or page.height <= 0:
                continue
            for block in page.blocks:
                if (page.page_no, block.block_id) in selected:
                    continue
                if "UNVERIFIED_LAYOUT_FOOTER" in block.semantic_reasons:
                    continue
                text = self._clean_text(block.text)
                if not (re.fullmatch(r"[\u4e00-\u9fff☑]{1,8}", self._compact(text))
                        and block.bbox.y0 >= page.height * 0.85
                        and block.block_type not in self.excluded_types
                        and block.block_role not in {"revision_artifact", "table_caption", "table_note"}):
                    continue
                height = block.bbox.y1 - block.bbox.y0
                # Handwritten glyphs here are larger than the printed body.
                # Do not route a small sentence continuation at a page break.
                body_heights = [b.bbox.y1-b.bbox.y0 for b in page.blocks
                                if b.block_type == "text" and len(self._compact(b.text)) >= 20
                                and b.bbox.y1 < page.height * 0.85]
                printed_height = (median(body_heights) if body_heights else
                                  median(body_height_ratios) * page.height if body_height_ratios else None)
                if printed_height is None or height < printed_height * 1.5:
                    continue
                peers = [c for c in prototypes
                         if abs((c.bbox.y0+c.bbox.y1)/2 - (block.bbox.y0+block.bbox.y1)/2) <= page.height * 0.045
                         and block.bbox.x0 - 15 <= (c.bbox.x0+c.bbox.x1)/2 <= block.bbox.x1 + 15
                         and 0.45 <= height / max(1, c.bbox.y1-c.bbox.y0) <= 2.2]
                if len({c.page_no for c in peers}) < 3:
                    continue
                footer_candidates.append(HeaderFooterCandidate(
                    slot="footer", text=text, page_no=page.page_no, bbox=block.bbox,
                    block_id=block.block_id, explicit=True, is_lower_footer_annotation=True,
                    is_handwritten_annotation=True, confidence=block.confidence,
                ))
                selected.add((page.page_no, block.block_id))

        entries: dict[str, list[HeaderFooterEntry]] = {}
        for slot, candidates in candidates_by_slot.items():
            slot_entries = self._non_page_number_entries(slot, candidates)
            page_number_entry = self._page_number_entry(slot, candidates)
            if page_number_entry is not None:
                slot_entries.append(page_number_entry)
            if slot_entries:
                entries[slot] = slot_entries
        return entries

    def _candidate(self, block: TextBlock, page_height: float, *, trusted_page_number: bool = False) -> HeaderFooterCandidate | None:
        # A layout-only footer can locate handwriting without reading all of
        # it. Visual comparison owns this candidate until text is verified.
        if "UNVERIFIED_LAYOUT_FOOTER" in block.semantic_reasons:
            return None
        if block.block_role in {"revision_artifact", "repeated_document_title", "table_caption", "table_note"}:
            return None
        text = self._clean_text(block.text)
        if not text:
            return None

        block_type = (block.block_type or "").lower()
        if block_type in self.excluded_types:
            return None
        is_handwritten_annotation = "ppstructure_short_annotation_repair" in (block.source or "")
        is_page_number = self._is_page_number(text) and not is_handwritten_annotation
        explicit_header = block_type in self.header_types
        explicit_footer = block_type in self.footer_types
        lower_footer_annotation = page_height > 0 and block.bbox.y0 >= page_height * self.footer_annotation_band
        if (block.layout_match_status or "") == "noise_unmatched" and not (explicit_footer and lower_footer_annotation):
            return None
        if self._is_bare_field_label(text):
            return None
        if not is_handwritten_annotation and self._looks_like_short_edge_noise(text, block, page_height):
            return None
        if is_page_number:
            if not trusted_page_number and (page_height <= 0 or block.bbox.y0 < page_height * self.page_number_footer_band):
                return None
            slot = "footer"
        else:
            slot = ""
        if not slot and explicit_header:
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
            is_handwritten_annotation=is_handwritten_annotation,
            confidence=block.confidence,
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
                    is_handwritten_annotation=any(candidate.is_handwritten_annotation for candidate in group),
                )
            )
        return entries

    def _merge_repeated_footer_annotation_variants(
        self,
        grouped: dict[str, list[HeaderFooterCandidate]],
    ) -> None:
        prototypes = [(key, group) for key, group in grouped.items() if self._is_footer_annotation_prototype(group)]
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
        if len({candidate.page_no for candidate in group}) < self.footer_variant_prototype_min_pages:
            return False
        if not all(candidate.is_lower_footer_annotation for candidate in group):
            return False
        return self._footer_annotation_cluster_is_consistent(group)

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
        if self._has_bounded_annotation_suffix(prototype_key, variant_key):
            return True
        return self._is_low_confidence_single_character_substitution(prototype_key, variant_key, variant)

    def _has_bounded_annotation_suffix(self, prototype_key: str, variant_key: str) -> bool:
        suffix = variant_key.removeprefix(prototype_key)
        return variant_key.startswith(prototype_key) and 1 <= len(suffix) <= 2

    def _is_low_confidence_single_character_substitution(
        self,
        prototype_key: str,
        variant_key: str,
        variant: HeaderFooterCandidate,
    ) -> bool:
        return (
            len(prototype_key) == len(variant_key)
            and len(prototype_key) >= 2
            and prototype_key[-1] == variant_key[-1]
            and variant.confidence is not None
            and variant.confidence <= self.footer_variant_substitution_max_confidence
            and sum(left != right for left, right in zip(prototype_key, variant_key, strict=True)) == 1
        )

    def _is_short_cjk_annotation(self, text: str) -> bool:
        return 2 <= len(text) <= self.footer_variant_max_characters and all(
            "\u4e00" <= char <= "\u9fff" for char in text
        )

    def _footer_annotation_position_matches(
        self,
        variant: HeaderFooterCandidate,
        prototype_group: list[HeaderFooterCandidate],
    ) -> bool:
        variant_center_x = (variant.bbox.x0 + variant.bbox.x1) / 2
        variant_center_y = (variant.bbox.y0 + variant.bbox.y1) / 2
        center_x, center_y = self._footer_annotation_cluster_center(prototype_group)
        return (
            abs(variant_center_x - center_x) <= self.footer_annotation_cluster_max_x_delta
            and abs(variant_center_y - center_y) <= self.footer_annotation_cluster_max_y_delta
        )

    def _footer_annotation_cluster_is_consistent(self, group: list[HeaderFooterCandidate]) -> bool:
        center_x, center_y = self._footer_annotation_cluster_center(group)
        return all(
            abs((candidate.bbox.x0 + candidate.bbox.x1) / 2 - center_x) <= self.footer_annotation_cluster_max_x_delta
            and abs((candidate.bbox.y0 + candidate.bbox.y1) / 2 - center_y)
            <= self.footer_annotation_cluster_max_y_delta
            for candidate in group
        )

    @staticmethod
    def _footer_annotation_cluster_center(group: list[HeaderFooterCandidate]) -> tuple[float, float]:
        return (
            median((candidate.bbox.x0 + candidate.bbox.x1) / 2 for candidate in group),
            median((candidate.bbox.y0 + candidate.bbox.y1) / 2 for candidate in group),
        )

    def _page_number_entry(
        self,
        slot: str,
        candidates: list[HeaderFooterCandidate],
    ) -> HeaderFooterEntry | None:
        page_numbers = self._sequenced_page_numbers([candidate for candidate in candidates if candidate.is_page_number])
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

    def _sequenced_page_numbers(
        self,
        candidates: list[HeaderFooterCandidate],
    ) -> list[HeaderFooterCandidate]:
        by_page: dict[int, HeaderFooterCandidate] = {}
        for candidate in sorted(candidates, key=lambda item: (item.page_no, item.bbox.y0, item.bbox.x0)):
            by_page.setdefault(candidate.page_no, candidate)
        ordered = list(by_page.values())
        if len(ordered) == 1:
            compact = self._compact(ordered[0].text)
            match = self.page_number_with_total_pattern.fullmatch(compact)
            if match and int(match.group("total")) == 1 and int(match.group("page")) == 1:
                return ordered
            return []

        values = [self._page_number_value(candidate.text) for candidate in ordered]
        supported_indexes: set[int] = set()
        for index in range(len(ordered) - 1):
            left = values[index]
            right = values[index + 1]
            page_delta = ordered[index + 1].page_no - ordered[index].page_no
            if left is not None and right is not None and page_delta > 0 and right - left == page_delta:
                supported_indexes.update({index, index + 1})
        return [candidate for index, candidate in enumerate(ordered) if index in supported_indexes]

    def _page_number_value(self, text: str) -> int | None:
        compact = self._compact(text)
        total_match = self.page_number_with_total_pattern.fullmatch(compact)
        if total_match:
            return int(total_match.group("page"))
        match = re.fullmatch(r"(?:第)?(\d+)(?:页)?", compact)
        return int(match.group(1)) if match else None

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
        return (
            self._is_fuzzy_matchable(left) and self._is_fuzzy_matchable(right)
            and self._page_overlap_score(left.evidences, right.evidences) > 0
            and self._position_similarity(left.evidences, right.evidences) >= 50
        )

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
            is_handwritten_annotation=entry.is_handwritten_annotation,
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
        annotation_flags = (
            ["HANDWRITTEN_ANNOTATION_REVIEW"]
            if (left.is_handwritten_annotation or right.is_handwritten_annotation)
            else []
        )
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
            review_flags=list(dict.fromkeys([*(review_flags or []), *annotation_flags])),
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
            review_flags=["HANDWRITTEN_ANNOTATION_REVIEW"] if entry.is_handwritten_annotation else [],
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
                    confidence=candidate.confidence if candidate.confidence is not None else 0.8,
                    text_confidence=candidate.confidence,
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
