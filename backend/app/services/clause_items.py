from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.models import CharBox, EvidenceBox


@dataclass
class ClauseItemFragment:
    text: str
    char_boxes: list[CharBox | None]
    page_number: int
    bbox: EvidenceBox
    source_block_id: str


@dataclass
class ClauseItem:
    clause_no: str
    title: str
    section_type: str = "main_contract"
    section_path: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    char_boxes: list[list[CharBox | None]] = field(default_factory=list)
    page_numbers: list[int] = field(default_factory=list)
    bboxes: list[EvidenceBox] = field(default_factory=list)
    source_block_ids: list[str] = field(default_factory=list)
    segmentation_reason: str = ""
    segmentation_confidence: float = 0.8
    split_flags: list[str] = field(default_factory=list)

    @classmethod
    def from_part(
        cls,
        *,
        clause_no: str,
        title: str,
        section_type: str,
        section_path: Sequence[str],
        text: str,
        char_boxes: list[CharBox | None],
        page_numbers: Iterable[int],
        bboxes: Iterable[EvidenceBox],
        source_block_ids: Iterable[str],
        segmentation_reason: str,
        segmentation_confidence: float,
        split_flags: Iterable[str],
    ) -> "ClauseItem":
        return cls(
            clause_no=clause_no,
            title=title,
            section_type=section_type,
            section_path=list(section_path),
            texts=[text],
            char_boxes=[char_boxes],
            page_numbers=list(page_numbers),
            bboxes=list(bboxes),
            source_block_ids=list(source_block_ids),
            segmentation_reason=segmentation_reason,
            segmentation_confidence=segmentation_confidence,
            split_flags=list(dict.fromkeys(split_flags)),
        )

    @property
    def is_empty(self) -> bool:
        return not self.texts

    @property
    def first_text(self) -> str:
        return self.texts[0] if self.texts else ""

    def append_part(
        self,
        *,
        text: str,
        char_boxes: list[CharBox | None],
        page_numbers: Iterable[int],
        bboxes: Iterable[EvidenceBox],
        source_block_ids: Iterable[str],
        split_flags: Iterable[str] = (),
    ) -> None:
        self.texts.append(text)
        self.char_boxes.append(char_boxes)
        self.page_numbers.extend(page_numbers)
        self.bboxes.extend(bboxes)
        self.source_block_ids.extend(source_block_ids)
        self.extend_split_flags(split_flags)

    def merge_from(self, source: "ClauseItem", reason: str) -> None:
        self.texts.extend(source.texts)
        self.char_boxes.extend(source.char_boxes)
        self.page_numbers.extend(source.page_numbers)
        self.bboxes.extend(source.bboxes)
        self.source_block_ids.extend(source.source_block_ids)
        self.extend_split_flags(source.split_flags)
        self.append_order_reason(reason)

    def pop_fragment(self, index: int) -> ClauseItemFragment:
        return ClauseItemFragment(
            text=self.texts.pop(index),
            char_boxes=self.char_boxes.pop(index),
            page_number=self.page_numbers.pop(index),
            bbox=self.bboxes.pop(index),
            source_block_id=self.source_block_ids.pop(index),
        )

    def insert_fragment(self, index: int, fragment: ClauseItemFragment) -> None:
        self.texts.insert(index, fragment.text)
        self.char_boxes.insert(index, fragment.char_boxes)
        self.page_numbers.insert(index, fragment.page_number)
        self.bboxes.insert(index, fragment.bbox)
        self.source_block_ids.insert(index, fragment.source_block_id)

    def extend_split_flags(self, flags: Iterable[str]) -> None:
        self.split_flags.extend(flag for flag in flags if flag not in self.split_flags)

    def append_order_reason(self, addition: str) -> None:
        if not self.segmentation_reason:
            self.segmentation_reason = f"order:{addition}"
            return
        if addition in self.segmentation_reason:
            return
        self.segmentation_reason = f"{self.segmentation_reason}|order:{addition}"
