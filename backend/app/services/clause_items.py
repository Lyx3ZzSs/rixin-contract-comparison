from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.models import CharBox, EvidenceBox


@dataclass
class ClauseItemFragment:
    text: str
    char_boxes: list[CharBox | None]
    page_numbers: list[int]
    bboxes: list[EvidenceBox]
    source_block_ids: list[str]

    @property
    def page_number(self) -> int:
        if self.page_numbers:
            return self.page_numbers[0]
        if self.bboxes:
            return self.bboxes[0].page_no
        return 0

    @property
    def bbox(self) -> EvidenceBox | None:
        return self.bboxes[0] if self.bboxes else None

    @property
    def source_block_id(self) -> str:
        return self.source_block_ids[0] if self.source_block_ids else ""


@dataclass
class ClauseItem:
    clause_no: str
    title: str
    section_type: str = "main_contract"
    section_path: list[str] = field(default_factory=list)
    fragments: list[ClauseItemFragment] = field(default_factory=list)
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
            fragments=[
                ClauseItemFragment(
                    text=text,
                    char_boxes=char_boxes,
                    page_numbers=list(page_numbers),
                    bboxes=list(bboxes),
                    source_block_ids=list(source_block_ids),
                )
            ],
            segmentation_reason=segmentation_reason,
            segmentation_confidence=segmentation_confidence,
            split_flags=list(dict.fromkeys(split_flags)),
        )

    @property
    def texts(self) -> list[str]:
        return [fragment.text for fragment in self.fragments]

    @property
    def char_boxes(self) -> list[list[CharBox | None]]:
        return [fragment.char_boxes for fragment in self.fragments]

    @property
    def page_numbers(self) -> list[int]:
        return [page_no for fragment in self.fragments for page_no in fragment.page_numbers]

    @property
    def bboxes(self) -> list[EvidenceBox]:
        return [bbox for fragment in self.fragments for bbox in fragment.bboxes]

    @property
    def source_block_ids(self) -> list[str]:
        return [block_id for fragment in self.fragments for block_id in fragment.source_block_ids]

    @property
    def is_empty(self) -> bool:
        return not self.fragments

    @property
    def first_text(self) -> str:
        return self.fragments[0].text if self.fragments else ""

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
        self.fragments.append(
            ClauseItemFragment(
                text=text,
                char_boxes=char_boxes,
                page_numbers=list(page_numbers),
                bboxes=list(bboxes),
                source_block_ids=list(source_block_ids),
            )
        )
        self.extend_split_flags(split_flags)

    def merge_from(self, source: "ClauseItem", reason: str) -> None:
        self.fragments.extend(source.fragments)
        self.extend_split_flags(source.split_flags)
        self.append_order_reason(reason)

    def pop_fragment(self, index: int) -> ClauseItemFragment:
        return self.fragments.pop(index)

    def insert_fragment(self, index: int, fragment: ClauseItemFragment) -> None:
        self.fragments.insert(index, fragment)

    def fragment_primary_evidence(self, index: int) -> EvidenceBox | None:
        return self.fragments[index].bbox

    def fragment_primary_page_number(self, index: int) -> int:
        return self.fragments[index].page_number

    def extend_split_flags(self, flags: Iterable[str]) -> None:
        self.split_flags.extend(flag for flag in flags if flag not in self.split_flags)

    def append_order_reason(self, addition: str) -> None:
        if not self.segmentation_reason:
            self.segmentation_reason = f"order:{addition}"
            return
        if addition in self.segmentation_reason:
            return
        self.segmentation_reason = f"{self.segmentation_reason}|order:{addition}"
