from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
from functools import lru_cache

from app.models import Document, Page, TextBlock
from app.services.pipeline_metrics import PerformanceRecorder


class PageAlignmentStatus(StrEnum):
    MATCHED = "MATCHED"
    MERGED = "MERGED"
    SPLIT = "SPLIT"
    INSERTED = "INSERTED"
    DELETED = "DELETED"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class PageAlignmentEntry:
    status: PageAlignmentStatus
    original_pages: tuple[int, ...]
    compare_pages: tuple[int, ...]
    alignment_score: float
    anchors: tuple[str, ...]
    reason: str

    def to_debug_payload(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "original_pages": list(self.original_pages),
            "compare_pages": list(self.compare_pages),
            "alignment_score": round(self.alignment_score, 4),
            "anchors": list(self.anchors),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PageAlignmentResult:
    entries: tuple[PageAlignmentEntry, ...]
    performance: dict[str, object] = field(default_factory=dict, compare=False, repr=False)

    def entry_for_original(self, page_no: int) -> PageAlignmentEntry | None:
        return next((entry for entry in self.entries if page_no in entry.original_pages), None)

    def entry_for_compare(self, page_no: int) -> PageAlignmentEntry | None:
        return next((entry for entry in self.entries if page_no in entry.compare_pages), None)

    def counterparts_for_original(self, page_no: int) -> tuple[int, ...]:
        entry = self.entry_for_original(page_no)
        return entry.compare_pages if entry is not None else ()

    def counterparts_for_compare(self, page_no: int) -> tuple[int, ...]:
        entry = self.entry_for_compare(page_no)
        return entry.original_pages if entry is not None else ()

    def relationship_score(self, original_pages: set[int], compare_pages: set[int]) -> float:
        if not original_pages or not compare_pages:
            return 0.0
        scores = [
            entry.alignment_score
            for entry in self.entries
            if original_pages.intersection(entry.original_pages)
            and compare_pages.intersection(entry.compare_pages)
            and entry.status
            in {
                PageAlignmentStatus.MATCHED,
                PageAlignmentStatus.MERGED,
                PageAlignmentStatus.SPLIT,
            }
        ]
        return max(scores, default=0.0)

    def to_debug_payload(self) -> dict[str, object]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.status.value] = counts.get(entry.status.value, 0) + 1
        return {
            "status_counts": counts,
            "entries": [entry.to_debug_payload() for entry in self.entries],
        }


@dataclass(frozen=True)
class _PageFingerprint:
    pages: tuple[int, ...]
    text: str
    anchors: tuple[str, ...]
    block_count: int
    layout_signature: tuple[tuple[int, int, str], ...]

    @property
    def stable(self) -> bool:
        return len(self.text) >= 30 and self.block_count >= 1


class PageSequenceAligner:
    """Monotonic page alignment supporting local merge/split pagination reflow."""

    matched_threshold = 0.46
    grouped_threshold = 0.50
    absent_threshold = 0.30
    gap_cost = 0.72
    weak_gap_cost = 0.42

    _noise_types = {"abandon", "formula", "footer", "page_footer"}
    _heading_types = {"title", "heading", "header", "table", "table_caption"}

    def __init__(self, *, cache_size: int = 50_000) -> None:
        self.cache_size = max(0, cache_size)

    def align(self, original: Document, compare: Document) -> PageAlignmentResult:
        # A worker owns all memoized methods for exactly one invocation. Even a
        # shared aligner can process different documents without stale entries.
        worker = copy.copy(self)
        recorder = PerformanceRecorder()
        memoized = ("_similarity", "_combine", "_ngrams", "_layout_similarity", "_anchor_similarity", "_text_ratio")
        for name in memoized:
            setattr(worker, name, lru_cache(maxsize=self.cache_size)(getattr(worker, name)))
        try:
            with recorder.measure("fingerprints"):
                original_items = [worker._fingerprint((page,)) for page in original.pages]
                compare_items = [worker._fingerprint((page,)) for page in compare.pages]
            with recorder.measure("dynamic_alignment"):
                entries = worker._dynamic_alignment(original_items, compare_items)
            recorder.set_counter("original_pages", len(original_items))
            recorder.set_counter("compare_pages", len(compare_items))
            recorder.set_counter("cache_size_per_operation", self.cache_size)
            for name in memoized:
                info = getattr(worker, name).cache_info()
                for key, value in info._asdict().items():
                    recorder.set_counter(f"{name.lstrip('_')}_cache_{key}", value)
            return PageAlignmentResult(tuple(entries), performance=recorder.snapshot())
        finally:
            # Clear retained fingerprints/text and break bound-method cycles.
            for name in memoized:
                getattr(worker, name).cache_clear()
                delattr(worker, name)

    def _dynamic_alignment(
        self,
        original: list[_PageFingerprint],
        compare: list[_PageFingerprint],
    ) -> list[PageAlignmentEntry]:
        n, m = len(original), len(compare)
        infinity = float("inf")
        costs = [[infinity] * (m + 1) for _ in range(n + 1)]
        moves: list[list[tuple[int, int, str, float, tuple[str, ...]] | None]] = [
            [None] * (m + 1) for _ in range(n + 1)
        ]
        costs[0][0] = 0.0

        for i in range(n + 1):
            for j in range(m + 1):
                current = costs[i][j]
                if current == infinity:
                    continue
                if i < n and j < m:
                    score, anchors = self._similarity(original[i], compare[j])
                    self._relax(costs, moves, i, j, 1, 1, "match", current + self._match_cost(score), score, anchors)
                if i + 1 < n and j < m:
                    if self._group_supported((original[i], original[i + 1]), compare[j]):
                        left = self._combine(original[i], original[i + 1])
                        score, anchors = self._similarity(left, compare[j])
                        self._relax(
                            costs,
                            moves,
                            i,
                            j,
                            2,
                            1,
                            "merge",
                            current + self._group_cost(score),
                            score,
                            anchors,
                        )
                if i < n and j + 1 < m:
                    if self._group_supported((compare[j], compare[j + 1]), original[i]):
                        right = self._combine(compare[j], compare[j + 1])
                        score, anchors = self._similarity(original[i], right)
                        self._relax(
                            costs,
                            moves,
                            i,
                            j,
                            1,
                            2,
                            "split",
                            current + self._group_cost(score),
                            score,
                            anchors,
                        )
                if i < n:
                    self._relax(
                        costs,
                        moves,
                        i,
                        j,
                        1,
                        0,
                        "delete",
                        current + self._gap_cost(original[i]),
                        0.0,
                        (),
                    )
                if j < m:
                    self._relax(
                        costs,
                        moves,
                        i,
                        j,
                        0,
                        1,
                        "insert",
                        current + self._gap_cost(compare[j]),
                        0.0,
                        (),
                    )

        raw: list[tuple[int, int, str, float, tuple[str, ...]]] = []
        i, j = n, m
        while i or j:
            move = moves[i][j]
            if move is None:
                raise RuntimeError("Page alignment backtracking failed.")
            di, dj, operation, score, anchors = move
            raw.append((i - di, j - dj, operation, score, anchors))
            i -= di
            j -= dj
        raw.reverse()

        entries: list[PageAlignmentEntry] = []
        for oi, ci, operation, score, anchors in raw:
            if operation == "match":
                status = PageAlignmentStatus.MATCHED if score >= self.matched_threshold else PageAlignmentStatus.UNCERTAIN
                entries.append(
                    PageAlignmentEntry(
                        status=status,
                        original_pages=original[oi].pages,
                        compare_pages=compare[ci].pages,
                        alignment_score=score,
                        anchors=anchors,
                        reason="sequence_1_to_1" if status == PageAlignmentStatus.MATCHED else "low_confidence_1_to_1",
                    )
                )
            elif operation in {"merge", "split"}:
                left = self._combine(original[oi], original[oi + 1]) if operation == "merge" else original[oi]
                right = compare[ci] if operation == "merge" else self._combine(compare[ci], compare[ci + 1])
                status = (
                    PageAlignmentStatus.MERGED if operation == "merge" else PageAlignmentStatus.SPLIT
                ) if score >= self.grouped_threshold else PageAlignmentStatus.UNCERTAIN
                entries.append(
                    PageAlignmentEntry(
                        status=status,
                        original_pages=left.pages,
                        compare_pages=right.pages,
                        alignment_score=score,
                        anchors=anchors,
                        reason=("pagination_2_to_1" if operation == "merge" else "pagination_1_to_2")
                        if status != PageAlignmentStatus.UNCERTAIN
                        else "low_confidence_group_alignment",
                    )
                )
            elif operation == "delete":
                entries.append(self._gap_entry(original[oi], compare, deleted=True))
            else:
                entries.append(self._gap_entry(compare[ci], original, deleted=False))
        return entries

    def _gap_entry(
        self,
        item: _PageFingerprint,
        other: list[_PageFingerprint],
        *,
        deleted: bool,
    ) -> PageAlignmentEntry:
        best_score = 0.0
        best_anchors: tuple[str, ...] = ()
        for candidate in other:
            score, anchors = self._similarity(item, candidate)
            if score > best_score:
                best_score, best_anchors = score, anchors
        confirmed_absent = item.stable and best_score < self.absent_threshold
        status = (
            PageAlignmentStatus.DELETED if deleted else PageAlignmentStatus.INSERTED
        ) if confirmed_absent else PageAlignmentStatus.UNCERTAIN
        return PageAlignmentEntry(
            status=status,
            original_pages=item.pages if deleted else (),
            compare_pages=() if deleted else item.pages,
            alignment_score=round(max(0.0, 1.0 - best_score), 4) if confirmed_absent else round(best_score, 4),
            anchors=best_anchors,
            reason=("stable_content_absent_after_sequence_search" if confirmed_absent else "page_evidence_not_reliably_observed"),
        )

    @staticmethod
    def _relax(
        costs: list[list[float]],
        moves: list[list[tuple[int, int, str, float, tuple[str, ...]] | None]],
        i: int,
        j: int,
        di: int,
        dj: int,
        operation: str,
        candidate_cost: float,
        score: float,
        anchors: tuple[str, ...],
    ) -> None:
        ni, nj = i + di, j + dj
        if candidate_cost + 1e-9 < costs[ni][nj]:
            costs[ni][nj] = candidate_cost
            moves[ni][nj] = (di, dj, operation, round(score, 4), anchors)

    @staticmethod
    def _match_cost(score: float) -> float:
        return max(0.05, 1.8 * (1.0 - score))

    @staticmethod
    def _group_cost(score: float) -> float:
        return 0.22 + 1.65 * (1.0 - score)

    def _gap_cost(self, item: _PageFingerprint) -> float:
        return self.gap_cost if item.stable else self.weak_gap_cost

    def _fingerprint(self, pages: tuple[Page, ...]) -> _PageFingerprint:
        blocks = [block for page in pages for block in page.blocks if self._meaningful(block, page)]
        blocks.sort(key=lambda block: (block.page_no, block.reading_order or 10_000, block.bbox.y0, block.bbox.x0))
        normalized_pairs = [(block, self._normalize(block.text)) for block in blocks]
        normalized_pairs = [(block, text) for block, text in normalized_pairs if text]
        blocks = [block for block, _text in normalized_pairs]
        normalized_blocks = [text for _block, text in normalized_pairs]
        text = "".join(normalized_blocks)[:20_000]
        anchors = self._anchors(blocks, normalized_blocks)
        layout = tuple(self._layout_token(block, pages) for block in blocks[:80])
        return _PageFingerprint(
            pages=tuple(page.page_no for page in pages),
            text=text,
            anchors=anchors,
            block_count=len(blocks),
            layout_signature=layout,
        )

    def _combine(self, left: _PageFingerprint, right: _PageFingerprint) -> _PageFingerprint:
        return _PageFingerprint(
            pages=(*left.pages, *right.pages),
            text=(left.text + right.text)[:20_000],
            anchors=tuple(dict.fromkeys((*left.anchors, *right.anchors)))[:12],
            block_count=left.block_count + right.block_count,
            layout_signature=(*left.layout_signature, *right.layout_signature)[:160],
        )

    def _similarity(
        self,
        left: _PageFingerprint,
        right: _PageFingerprint,
    ) -> tuple[float, tuple[str, ...]]:
        if not left.text or not right.text:
            layout_score = self._layout_similarity(left.layout_signature, right.layout_signature)
            return round(layout_score * 0.25, 4), ()
        ratio = self._text_ratio(left.text, right.text)
        left_grams = self._ngrams(left.text)
        right_grams = self._ngrams(right.text)
        common = len(left_grams.intersection(right_grams))
        containment = common / max(1, min(len(left_grams), len(right_grams)))
        balance = min(len(left.text), len(right.text)) / max(len(left.text), len(right.text))
        content_score = max(ratio, containment * (0.78 + 0.22 * balance))
        anchor_score, shared_anchors = self._anchor_similarity(left.anchors, right.anchors)
        layout_score = self._layout_similarity(left.layout_signature, right.layout_signature)
        score = content_score * 0.86 + anchor_score * 0.10 + layout_score * 0.04
        if anchor_score >= 0.85 and content_score >= 0.30:
            score = max(score, 0.50 + 0.30 * content_score)
        return round(min(1.0, score), 4), shared_anchors

    def _group_supported(
        self,
        parts: tuple[_PageFingerprint, _PageFingerprint],
        target: _PageFingerprint,
    ) -> bool:
        # A blank form date at the head of a page group cannot belong to a
        # date field at the *end* of the following form. Respect text order
        # even when the longer page dominates the similarity score.
        if (not parts[0].stable and parts[0].text
                and re.fullmatch(r"[年月日_]+", parts[0].text)
                and not target.text.startswith(parts[0].text)):
            return False
        part_scores = [self._similarity(part, target)[0] for part in parts]
        # Reject unsupported parts before comparing their longer combined text.
        # These guards and the DP transitions retain their original semantics.
        for part, score in zip(parts, part_scores, strict=True):
            if part.stable and score < 0.35:
                return False
            if not part.stable and part.text:
                grams = self._ngrams(part.text, size=min(3, len(part.text)))
                target_grams = self._ngrams(target.text, size=min(3, len(part.text)))
                if not grams.intersection(target_grams) and score < 0.18:
                    return False
        combined_score = self._similarity(self._combine(*parts), target)[0]
        if all(part.stable for part in parts):
            return combined_score >= max(part_scores) + 0.005
        return combined_score >= max(part_scores) - 0.12

    def _anchors(self, blocks: list[TextBlock], normalized: list[str]) -> tuple[str, ...]:
        candidates: list[str] = []
        for block, text in zip(blocks, normalized, strict=False):
            if 3 <= len(text) <= 100 and (
                (block.block_type or "").lower() in self._heading_types
                or len(text) <= 36
                or re.match(r"^(第[一二三四五六七八九十百\d]+|附件|项目|合同)", text)
            ):
                candidates.append(text[:64])
        edge = [*normalized[:3], *normalized[-3:]]
        return tuple(dict.fromkeys([text[:64] for text in (*candidates, *edge) if len(text) >= 3]))[:12]

    def _anchor_similarity(self, left: tuple[str, ...], right: tuple[str, ...]) -> tuple[float, tuple[str, ...]]:
        if not left or not right:
            return 0.0, ()
        matches: list[tuple[float, str]] = []
        for left_anchor in left:
            best = max((self._text_ratio(left_anchor, item) for item in right), default=0.0)
            if best >= 0.72:
                matches.append((best, left_anchor[:48]))
        score = sum(item[0] for item in matches) / max(1, min(len(left), len(right)))
        return min(1.0, score), tuple(item[1] for item in sorted(matches, reverse=True)[:4])

    @staticmethod
    def _text_ratio(left: str, right: str) -> float:
        # SequenceMatcher may differ with argument order; never sort this key.
        return SequenceMatcher(None, left, right).ratio()

    @staticmethod
    def _layout_similarity(
        left: tuple[tuple[int, int, str], ...],
        right: tuple[tuple[int, int, str], ...],
    ) -> float:
        if not left or not right:
            return 0.0
        left_set, right_set = set(left), set(right)
        return len(left_set & right_set) / max(1, len(left_set | right_set))

    @staticmethod
    def _layout_token(block: TextBlock, pages: tuple[Page, ...]) -> tuple[int, int, str]:
        page = next((item for item in pages if item.page_no == block.page_no), pages[0])
        width, height = max(1.0, page.width), max(1.0, page.height)
        cx = (block.bbox.x0 + block.bbox.x1) / 2 / width
        cy = (block.bbox.y0 + block.bbox.y1) / 2 / height
        return min(4, int(cx * 5)), min(7, int(cy * 8)), (block.block_type or "text").lower()

    def _meaningful(self, block: TextBlock, page: Page) -> bool:
        block_type = (block.block_type or "").lower()
        if block_type in self._noise_types:
            return False
        text = self._normalize(block.text)
        if len(text) < 2:
            return False
        if re.fullmatch(r"第?\d+页|共\d+页第\d+页|\d+/\d+|\d+", text):
            if page.height and (block.bbox.y0 < page.height * 0.08 or block.bbox.y1 > page.height * 0.92):
                return False
        return True

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "").lower()
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)

    @staticmethod
    def _ngrams(text: str, size: int = 3) -> set[str]:
        if len(text) <= size:
            return {text} if text else set()
        return {text[index : index + size] for index in range(len(text) - size + 1)}
