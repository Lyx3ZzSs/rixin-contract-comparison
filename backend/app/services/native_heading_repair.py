from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import fitz

from app.models import BBox, CharBox, Document, Page, TextBlock


HEADING_RE = re.compile(r"^\s*(?P<number>\d{1,2})\s*[.．、]\s*(?P<title>[\u4e00-\u9fffA-Za-z][^\n]{1,23})\s*$")
BARE_RE = re.compile(r"^\s*(?P<number>\d{1,2})\s*[.．、]\s*$")
VALUE_RE = re.compile(r"(?:\d{4}\s*年|\d+(?:\.\d+)?\s*(?:元|万元|%|天|月|年|份|项|台|套))")
TITLE_BLOCK_TYPES = {"paragraph_title", "doc_title", "title"}


def normalize_heading_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\s，。；：、,.．;:]+", "", normalized).lower()


@dataclass(frozen=True)
class NativeHeadingCandidate:
    page_no: int
    number: str
    title: str
    text: str
    bbox: BBox
    char_boxes: tuple[CharBox, ...]


@dataclass(frozen=True)
class NativeHeadingIndex:
    candidates: tuple[NativeHeadingCandidate, ...] = ()
    warning: str = ""

    def contains_exact(self, number: str, title: str, pages: set[int]) -> bool:
        title_key = normalize_heading_text(title)
        matches = [
            item
            for item in self.candidates
            if item.page_no in pages
            and item.number == str(number).strip()
            and normalize_heading_text(item.title) == title_key
        ]
        return len(matches) == 1


@dataclass
class NativeHeadingRepairResult:
    decisions: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def repaired_count(self) -> int:
        return sum(1 for item in self.decisions if item.get("action") == "repaired")

    def to_debug_payload(self) -> dict[str, object]:
        return {
            "repaired_count": self.repaired_count,
            "decisions": self.decisions,
            "warnings": self.warnings,
        }


def load_native_heading_index(path: str | Path) -> NativeHeadingIndex:
    resolved = Path(path)
    try:
        stat = resolved.stat()
    except OSError as exc:
        return NativeHeadingIndex(warning=f"native PDF unavailable: {exc}")
    return _load_native_heading_index_cached(str(resolved.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=64)
def _load_native_heading_index_cached(path: str, mtime_ns: int, size: int) -> NativeHeadingIndex:
    del mtime_ns, size
    try:
        pdf = fitz.open(path)
    except Exception as exc:
        return NativeHeadingIndex(warning=f"native PDF open failed: {exc}")
    candidates: list[NativeHeadingCandidate] = []
    try:
        for page_no, pdf_page in enumerate(pdf, start=1):
            for text, bbox, char_boxes in _native_lines(pdf_page, page_no):
                match = HEADING_RE.fullmatch(unicodedata.normalize("NFKC", text).strip())
                if match is None:
                    continue
                title = match.group("title").strip()
                compact_title = re.sub(r"\s+", "", title)
                if not 2 <= len(compact_title) <= 24:
                    continue
                if VALUE_RE.search(text) or re.search(r"[。；;：:]$", title):
                    continue
                candidates.append(
                    NativeHeadingCandidate(
                        page_no=page_no,
                        number=match.group("number"),
                        title=title,
                        text=text,
                        bbox=bbox,
                        char_boxes=tuple(char_boxes),
                    )
                )
    except Exception as exc:
        return NativeHeadingIndex(warning=f"native PDF extraction failed: {exc}")
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return NativeHeadingIndex(candidates=tuple(candidates))


def _native_lines(pdf_page, page_no: int) -> list[tuple[str, BBox, list[CharBox]]]:
    result: list[tuple[str, BBox, list[CharBox]]] = []
    raw = pdf_page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            chars: list[tuple[str, tuple[float, float, float, float]]] = []
            for span in line.get("spans", []):
                for item in span.get("chars", []):
                    value = str(item.get("c") or "")
                    bbox = item.get("bbox")
                    if value and isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                        chars.append((value, (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))))
            text = "".join(value for value, _ in chars).strip()
            if not text:
                continue
            leading = len("".join(value for value, _ in chars)) - len("".join(value for value, _ in chars).lstrip())
            boxes = [
                CharBox(
                    char=value,
                    page_no=page_no,
                    bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
                    text_index=index - leading,
                )
                for index, (value, box) in enumerate(chars)
                if leading <= index < leading + len(text)
            ]
            x0 = min(item.bbox.x0 for item in boxes)
            y0 = min(item.bbox.y0 for item in boxes)
            x1 = max(item.bbox.x1 for item in boxes)
            y1 = max(item.bbox.y1 for item in boxes)
            result.append((text, BBox(x0=x0, y0=y0, x1=x1, y1=y1), boxes))
    return result


class NativeHeadingRepairService:
    def repair(self, document: Document) -> NativeHeadingRepairResult:
        result = NativeHeadingRepairResult()
        index = load_native_heading_index(document.path)
        if index.warning:
            result.warnings.append(index.warning)
            return result
        pages = {page.page_no: page for page in document.pages}
        grouped: dict[tuple[int, str], list[NativeHeadingCandidate]] = {}
        for candidate in index.candidates:
            grouped.setdefault((candidate.page_no, candidate.number), []).append(candidate)
        for (page_no, number), candidates in grouped.items():
            page = pages.get(page_no)
            if page is None:
                continue
            bare = self._bare_block(page, number)
            if bare is None:
                continue
            if len(candidates) != 1:
                result.decisions.append({"action": "kept", "page_no": page_no, "number": number, "reason": "ambiguous_native_heading"})
                continue
            candidate = candidates[0]
            if self._has_conflicting_title(page, number, candidate.title, bare):
                result.decisions.append({"action": "kept", "page_no": page_no, "number": number, "reason": "conflicting_ocr_title"})
                continue
            if not self._geometry_matches(bare.bbox, candidate.bbox):
                result.decisions.append({"action": "kept", "page_no": page_no, "number": number, "reason": "geometry_mismatch"})
                continue
            if not self._has_local_structure(document, page, bare, number):
                result.decisions.append({"action": "kept", "page_no": page_no, "number": number, "reason": "missing_heading_context"})
                continue
            bare.text = candidate.text
            bare.bbox = BBox(
                x0=min(bare.bbox.x0, candidate.bbox.x0),
                y0=min(bare.bbox.y0, candidate.bbox.y0),
                x1=max(bare.bbox.x1, candidate.bbox.x1),
                y1=max(bare.bbox.y1, candidate.bbox.y1),
            )
            bare.char_boxes = list(candidate.char_boxes)
            bare.source = "+".join(part for part in [bare.source, "native_heading_repair"] if part)
            bare.semantic_reasons = [*bare.semantic_reasons, f"native_heading_repair:{number}"]
            result.decisions.append({"action": "repaired", "page_no": page_no, "number": number, "title": candidate.title, "block_id": bare.block_id, "reason": "exact_native_heading"})
        return result

    @staticmethod
    def _bare_block(page: Page, number: str) -> TextBlock | None:
        matches = [block for block in page.blocks if (match := BARE_RE.fullmatch(unicodedata.normalize("NFKC", block.text).strip())) and match.group("number") == number]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _has_conflicting_title(page: Page, number: str, title: str, bare: TextBlock) -> bool:
        expected = normalize_heading_text(f"{number}{title}")
        for block in page.blocks:
            if block is bare:
                continue
            text_key = normalize_heading_text(block.text)
            if text_key.startswith(number) and text_key != expected and not text_key.startswith(f"{number}1"):
                return True
        return False

    @staticmethod
    def _geometry_matches(left: BBox, right: BBox) -> bool:
        left_center = (left.y0 + left.y1) / 2
        right_center = (right.y0 + right.y1) / 2
        height = max(left.y1 - left.y0, right.y1 - right.y0, 1.0)
        return abs(left.x0 - right.x0) <= 36.0 and abs(left_center - right_center) <= height * 1.5

    @staticmethod
    def _has_local_structure(document: Document, page: Page, bare: TextBlock, number: str) -> bool:
        if bare.block_type in TITLE_BLOCK_TYPES:
            return True
        number_value = int(number)
        context_pages = {page.page_no, page.page_no + 1}
        texts = [block.text for item in document.pages if item.page_no in context_pages for block in item.blocks]
        child = re.compile(rf"^\s*{number_value}\.\d+")
        next_parent = re.compile(rf"^\s*{number_value + 1}\s*[.．、]")
        return any(child.match(text) for text in texts) or any(next_parent.match(text) for text in texts)
