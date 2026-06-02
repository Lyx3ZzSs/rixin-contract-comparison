from __future__ import annotations

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


@dataclass(frozen=True)
class HeaderFooterEntry:
    slot: str
    text: str
    key: str
    evidences: list[EvidenceBox]
    is_page_number: bool = False


class HeaderFooterComparator:
    """Builds audit diffs for document headers and footers."""

    header_types = {"header", "page_header"}
    footer_types = {"footer", "page_footer"}
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
    page_number_pattern = re.compile(
        r"^(?:第?\s*\d+\s*页?|共\s*\d+\s*页\s*第\s*\d+\s*页)$"
    )
    page_number_with_total_pattern = re.compile(
        r"^共\s*(?P<total>\d+)\s*页\s*第\s*(?P<page>\d+)\s*页$"
    )

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
        slot = ""
        if block_type in self.header_types:
            slot = "header"
        elif block_type in self.footer_types:
            slot = "footer"
        elif page_height > 0:
            near_top = block.bbox.y1 <= page_height * 0.08
            near_bottom = block.bbox.y0 >= page_height * 0.92
            if near_top and not self._looks_like_clause_start(text):
                slot = "header"
            elif near_bottom:
                slot = "footer"
        if not slot:
            return None

        return HeaderFooterCandidate(
            slot=slot,
            text=text,
            page_no=block.page_no,
            bbox=block.bbox,
            block_id=block.block_id,
            is_page_number=self._is_page_number(text),
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

        entries: list[HeaderFooterEntry] = []
        for key, group in sorted(grouped.items(), key=lambda item: self._entry_sort_key(item[1])):
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

        if len(unmatched_original) == 1 and len(unmatched_compare) == 1:
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

    def _modify_diff(self, left: HeaderFooterEntry, right: HeaderFooterEntry, index: int) -> DiffItem:
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
