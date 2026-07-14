from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import fitz

from app.models import BBox, CharBox, Document, Page, TextBlock


HEADING_RE = re.compile(r"^\s*(?P<number>\d{1,2})\s*[.．、]\s*(?P<title>[\u4e00-\u9fffA-Za-z][^\n]{1,23})\s*$")
TOP_LEVEL_LINE_RE = re.compile(
    r"^\s*(?P<number>\d{1,2})\s*[.．、]\s*(?P<title>[\u4e00-\u9fffA-Za-z][^\n]*)\s*$"
)
BARE_RE = re.compile(r"^\s*(?P<number>\d{1,2})\s*[.．、]\s*$")
NUMBERED_LINE_RE = re.compile(
    r"^\s*\d{1,2}(?:\.\d+)*(?:\s*[.．、]\s*|\s+)[\u4e00-\u9fffA-Za-z]"
)
APPENDIX_LABEL_RE = re.compile(r"^(?:附件|附录|附表)\s*[一二三四五六七八九十百千万0-9]+\s*$")
ATTACHMENT_CATALOG_ENTRY_RE = re.compile(
    r"^(?P<label>(?:附件|附录|附表)\s*[一二三四五六七八九十百千万0-9]+)\s*[:：]\s*(?P<title>[^\n]{2,48})$"
)
SAFETY_AGREEMENT_TITLE_RE = re.compile(r"^安全生产(?:管理)?协议(?:[（(][^()（）]{0,16}[）)])?$")
TITLE_METADATA_RE = re.compile(r"^(?:项目名称|甲方|乙方|签订地点|签订日期|合同编号|协议有效期)\s*[:：]")
VALUE_RE = re.compile(r"(?:\d{4}\s*年|\d+(?:\.\d+)?\s*(?:元|万元|%|天|月|年|份|项|台|套))")
TITLE_BLOCK_TYPES = {"paragraph_title", "doc_title", "title"}
SPLIT_BASELINE_TOLERANCE = 0.4
SPLIT_GAP_HEIGHT_RATIO = 2.0
SPLIT_FONT_SIZE_TOLERANCE_RATIO = 0.08
SPLIT_BODY_WINDOW_HEIGHT_RATIO = 12.0
SPLIT_BODY_MIN_LINES = 2
SPLIT_BODY_MIN_CHARS = 16
SPLIT_BOTTOM_REGION_RATIO = 0.8
SPLIT_NEXT_PAGE_TOP_REGION_RATIO = 0.25
MULTILINE_TITLE_TOP_REGION_RATIO = 0.45
MULTILINE_TITLE_MAX_LINES = 4
MULTILINE_TITLE_MIN_LINES = 2
MULTILINE_TITLE_MAX_GAP_HEIGHT_RATIO = 2.5


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
class NativeSectionTitleCandidate:
    page_no: int
    text: str
    bbox: BBox
    char_boxes: tuple[CharBox, ...]
    block_type: str = "paragraph_title"


@dataclass(frozen=True)
class NativeSpanStyle:
    size: float
    font: str
    flags: int
    char_count: int


@dataclass(frozen=True)
class NativeLineRecord:
    text: str
    bbox: BBox
    char_boxes: tuple[CharBox, ...]
    span_styles: tuple[NativeSpanStyle, ...]


@dataclass(frozen=True)
class NativeBodyStyleBaseline:
    size: float
    emphasized: bool
    char_count: int
    line_count: int


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
        return sum(1 for item in self.decisions if item.get("action") in {"repaired", "inserted"})

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


def load_native_section_title_candidates(path: str | Path) -> tuple[NativeSectionTitleCandidate, ...]:
    resolved = Path(path)
    try:
        stat = resolved.stat()
    except OSError:
        return ()
    return _load_native_section_title_candidates_cached(str(resolved.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=64)
def _load_native_heading_index_cached(path: str, mtime_ns: int, size: int) -> NativeHeadingIndex:
    del mtime_ns, size
    try:
        pdf = fitz.open(path)
    except Exception as exc:
        return NativeHeadingIndex(warning=f"native PDF open failed: {exc}")
    candidates: list[NativeHeadingCandidate] = []
    try:
        page_records: list[tuple[int, float, list[NativeLineRecord]]] = []
        for page_no, pdf_page in enumerate(pdf, start=1):
            lines = _native_line_records(pdf_page, page_no)
            page_records.append((page_no, float(pdf_page.rect.height), lines))
        for page_index, (page_no, page_height, lines) in enumerate(page_records):
            candidates.extend(
                candidate
                for line in lines
                if (
                    candidate := _candidate_from_line(
                        page_no,
                        line.text,
                        line.bbox,
                        line.char_boxes,
                    )
                )
                is not None
            )
            next_page = page_records[page_index + 1] if page_index + 1 < len(page_records) else None
            candidates.extend(
                _split_line_candidates(
                    page_no,
                    lines,
                    page_height=page_height,
                    next_page_lines=next_page[2] if next_page is not None else None,
                    next_page_height=next_page[1] if next_page is not None else None,
                )
            )
    except Exception as exc:
        return NativeHeadingIndex(warning=f"native PDF extraction failed: {exc}")
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return NativeHeadingIndex(candidates=tuple(_deduplicate_candidates(candidates)))


@lru_cache(maxsize=64)
def _load_native_section_title_candidates_cached(
    path: str,
    mtime_ns: int,
    size: int,
) -> tuple[NativeSectionTitleCandidate, ...]:
    del mtime_ns, size
    try:
        pdf = fitz.open(path)
    except Exception:
        return ()
    candidates: list[NativeSectionTitleCandidate] = []
    try:
        for page_no, pdf_page in enumerate(pdf, start=1):
            page_width = float(pdf_page.rect.width)
            page_height = float(pdf_page.rect.height)
            lines = _native_line_records(pdf_page, page_no)
            for line in lines:
                text = unicodedata.normalize("NFKC", line.text).strip()
                if not _is_missing_section_title_candidate(text, line.bbox, page_height):
                    continue
                candidates.append(
                    NativeSectionTitleCandidate(
                        page_no=page_no,
                        text=text,
                        bbox=line.bbox,
                        char_boxes=line.char_boxes,
                        block_type="text" if _is_attachment_catalog_entry(text) else "paragraph_title",
                    )
                )
            candidates.extend(
                _multiline_native_title_candidates(
                    page_no,
                    lines,
                    page_width=page_width,
                    page_height=page_height,
                )
            )
    except Exception:
        return ()
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return tuple(_deduplicate_section_title_candidates(candidates))


def _is_missing_section_title_candidate(text: str, bbox: BBox, page_height: float) -> bool:
    if not text:
        return False
    if _is_attachment_catalog_entry(text):
        return True
    if bbox.y0 > page_height * 0.20:
        return False
    return bool(APPENDIX_LABEL_RE.fullmatch(text) or SAFETY_AGREEMENT_TITLE_RE.fullmatch(text))


def _is_attachment_catalog_entry(text: str) -> bool:
    match = ATTACHMENT_CATALOG_ENTRY_RE.fullmatch(text)
    if match is None:
        return False
    title = match.group("title").strip()
    return bool(title and not title.endswith(("。", "；", ";", "，", ",")))


def _multiline_native_title_candidates(
    page_no: int,
    lines: list[NativeLineRecord],
    *,
    page_width: float,
    page_height: float,
) -> list[NativeSectionTitleCandidate]:
    ordered = sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))
    candidates: list[NativeSectionTitleCandidate] = []
    for start, first in enumerate(ordered):
        if not _is_multiline_title_line(first, page_width, page_height):
            continue
        group = [first]
        for following in ordered[start + 1:start + MULTILINE_TITLE_MAX_LINES]:
            if not _is_multiline_title_line(following, page_width, page_height):
                break
            if not _same_multiline_title_style(group[-1], following):
                break
            if not _visually_contiguous_title_lines(group[-1], following):
                break
            group.append(following)
        if len(group) < MULTILINE_TITLE_MIN_LINES:
            continue
        if not _has_multiline_title_style_evidence(group, ordered):
            continue
        block_type = (
            "text"
            if _has_preceding_attachment_context(group[0], ordered)
            else "doc_title"
        )
        candidates.extend(
            NativeSectionTitleCandidate(
                page_no=page_no,
                text=unicodedata.normalize("NFKC", line.text).strip(),
                bbox=line.bbox,
                char_boxes=line.char_boxes,
                # Attachment titles stay in the surrounding attachment clause;
                # other centered multi-line titles are cover metadata.
                block_type=block_type,
            )
            for line in group
        )
    return candidates


def _is_multiline_title_line(line: NativeLineRecord, page_width: float, page_height: float) -> bool:
    text = unicodedata.normalize("NFKC", line.text).strip()
    compact = re.sub(r"\s+", "", text)
    style = _dominant_span_style(line)
    if (
        style is None
        or not 2 <= len(compact) <= 40
        or line.bbox.y0 > page_height * MULTILINE_TITLE_TOP_REGION_RATIO
        or TITLE_METADATA_RE.match(text)
        or text.endswith(("。", "；", ";", "：", ":", "，", ","))
    ):
        return False
    center_x = (line.bbox.x0 + line.bbox.x1) / 2
    return abs(center_x - page_width / 2) <= page_width * 0.28


def _same_multiline_title_style(left: NativeLineRecord, right: NativeLineRecord) -> bool:
    left_style = _dominant_span_style(left)
    right_style = _dominant_span_style(right)
    if left_style is None or right_style is None:
        return False
    tolerance = max(
        0.75,
        max(left_style.size, right_style.size) * SPLIT_FONT_SIZE_TOLERANCE_RATIO,
    )
    return abs(left_style.size - right_style.size) <= tolerance


def _visually_contiguous_title_lines(left: NativeLineRecord, right: NativeLineRecord) -> bool:
    gap = right.bbox.y0 - left.bbox.y1
    height = max(left.bbox.y1 - left.bbox.y0, right.bbox.y1 - right.bbox.y0, 1.0)
    return 0 <= gap <= height * MULTILINE_TITLE_MAX_GAP_HEIGHT_RATIO


def _has_multiline_title_style_evidence(
    title_lines: list[NativeLineRecord],
    page_lines: list[NativeLineRecord],
) -> bool:
    title_style = _dominant_span_style(title_lines[0])
    if title_style is None:
        return False
    title_ids = {id(line) for line in title_lines}
    body_styles = _style_baselines([line for line in page_lines if id(line) not in title_ids])
    if not body_styles:
        return False
    title_is_emphasized = _is_emphasized_style(title_style)
    return any(
        title_style.size - body_style.size >= max(1.0, body_style.size * 0.12)
        or (title_is_emphasized and not body_style.emphasized)
        for body_style in body_styles
    )


def _has_preceding_attachment_context(
    title_line: NativeLineRecord,
    page_lines: list[NativeLineRecord],
) -> bool:
    return any(
        line.bbox.y1 <= title_line.bbox.y0
        and (
            APPENDIX_LABEL_RE.fullmatch(unicodedata.normalize("NFKC", line.text).strip())
            or _is_attachment_catalog_entry(unicodedata.normalize("NFKC", line.text).strip())
        )
        for line in page_lines
    )


def _candidate_from_line(
    page_no: int,
    text: str,
    bbox: BBox,
    char_boxes: list[CharBox] | tuple[CharBox, ...],
) -> NativeHeadingCandidate | None:
    match = HEADING_RE.fullmatch(unicodedata.normalize("NFKC", text).strip())
    if match is None:
        return None
    title = match.group("title").strip()
    if not _is_valid_native_title(title) or VALUE_RE.search(text):
        return None
    return NativeHeadingCandidate(
        page_no=page_no,
        number=match.group("number"),
        title=title,
        text=text,
        bbox=bbox,
        char_boxes=tuple(char_boxes),
    )


def _is_valid_native_title(title: str) -> bool:
    normalized = unicodedata.normalize("NFKC", title or "").strip()
    compact = re.sub(r"\s+", "", normalized)
    return bool(
        2 <= len(compact) <= 24
        and re.match(r"^[\u4e00-\u9fffA-Za-z]", normalized)
        and not VALUE_RE.search(normalized)
        and not re.search(r"[。；;：:]$", normalized)
    )


def _is_valid_conflict_title(title: str) -> bool:
    normalized = unicodedata.normalize("NFKC", title or "").strip()
    terminal_text = re.sub(r'''[\s"'”’」』】）》〉〕］）)\]}]+$''', "", normalized)
    return bool(
        normalized
        and re.match(r"^[\u4e00-\u9fffA-Za-z]", normalized)
        and not VALUE_RE.search(normalized)
        and not re.search(r"[。！？!?；;：:，,、….．]$", terminal_text)
    )


def _split_line_candidates(
    page_no: int,
    lines: list[NativeLineRecord],
    *,
    page_height: float | None = None,
    next_page_lines: list[NativeLineRecord] | None = None,
    next_page_height: float | None = None,
) -> list[NativeHeadingCandidate]:
    candidates: list[NativeHeadingCandidate] = []
    for bare_line in lines:
        if BARE_RE.fullmatch(unicodedata.normalize("NFKC", bare_line.text).strip()) is None:
            continue
        titles = [
            line
            for line in lines
            if _is_valid_native_title(line.text)
            and _is_same_heading_line(bare_line.bbox, line.bbox)
        ]
        if len(titles) != 1:
            continue
        title_line = titles[0]
        if not _has_split_heading_style_evidence(
            bare_line,
            title_line,
            lines,
            page_height=page_height,
            next_page_lines=next_page_lines,
            next_page_height=next_page_height,
        ):
            continue
        combined_text = f"{bare_line.text} {title_line.text}"
        title_offset = len(bare_line.text) + 1
        combined_boxes = [
            *bare_line.char_boxes,
            *[
                char_box.model_copy(
                    update={
                        "text_index": title_offset
                        + (char_box.text_index if char_box.text_index is not None else index)
                    }
                )
                for index, char_box in enumerate(title_line.char_boxes)
            ],
        ]
        combined_bbox = BBox(
            x0=min(bare_line.bbox.x0, title_line.bbox.x0),
            y0=min(bare_line.bbox.y0, title_line.bbox.y0),
            x1=max(bare_line.bbox.x1, title_line.bbox.x1),
            y1=max(bare_line.bbox.y1, title_line.bbox.y1),
        )
        candidate = _candidate_from_line(
            page_no,
            combined_text,
            combined_bbox,
            combined_boxes,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _has_split_heading_style_evidence(
    bare_line: NativeLineRecord,
    title_line: NativeLineRecord,
    lines: list[NativeLineRecord],
    *,
    page_height: float | None = None,
    next_page_lines: list[NativeLineRecord] | None = None,
    next_page_height: float | None = None,
) -> bool:
    bare_style = _dominant_span_style(bare_line)
    title_style = _dominant_span_style(title_line)
    if bare_style is None or title_style is None:
        return False
    size_tolerance = max(
        0.75,
        max(bare_style.size, title_style.size) * SPLIT_FONT_SIZE_TOLERANCE_RATIO,
    )
    if abs(bare_style.size - title_style.size) > size_tolerance:
        return False
    body_styles = _body_style_baselines(bare_line, title_line, lines)
    if not body_styles:
        heading_bottom = max(bare_line.bbox.y1, title_line.bbox.y1)
        if (
            page_height is None
            or heading_bottom < page_height * SPLIT_BOTTOM_REGION_RATIO
            or not next_page_lines
            or next_page_height is None
        ):
            return False
        body_styles = _next_page_body_style_baselines(
            bare_line,
            title_line,
            next_page_lines,
            next_page_height,
        )
        if not body_styles:
            return False
    title_is_emphasized = _is_emphasized_style(title_style)
    return all(
        title_style.size - body_style.size
        >= max(1.0, body_style.size * 0.12)
        or (title_is_emphasized and not body_style.emphasized)
        for body_style in body_styles
    )


def _body_style_baselines(
    bare_line: NativeLineRecord,
    title_line: NativeLineRecord,
    lines: list[NativeLineRecord],
) -> tuple[NativeBodyStyleBaseline, ...]:
    heading_bottom = max(bare_line.bbox.y1, title_line.bbox.y1)
    heading_height = max(
        bare_line.bbox.y1 - bare_line.bbox.y0,
        title_line.bbox.y1 - title_line.bbox.y0,
        1.0,
    )
    evidence_lines = [
        line
        for line in lines
        if _is_body_style_evidence_line(
            line,
            bare_line=bare_line,
            title_line=title_line,
            heading_bottom=heading_bottom,
            heading_height=heading_height,
        )
    ]
    return _style_baselines(evidence_lines)


def _next_page_body_style_baselines(
    bare_line: NativeLineRecord,
    title_line: NativeLineRecord,
    lines: list[NativeLineRecord],
    page_height: float,
) -> tuple[NativeBodyStyleBaseline, ...]:
    heading_height = max(
        bare_line.bbox.y1 - bare_line.bbox.y0,
        title_line.bbox.y1 - title_line.bbox.y0,
        1.0,
    )
    evidence_lines: list[NativeLineRecord] = []
    top_lines = sorted(
        (
            line
            for line in lines
            if 0 <= line.bbox.y0 <= page_height * SPLIT_NEXT_PAGE_TOP_REGION_RATIO
        ),
        key=lambda line: (line.bbox.y0, line.bbox.x0),
    )
    credible_body_styles = _style_baselines([
        line
        for line in top_lines
        if not _is_explicit_next_page_boundary(line.text)
        and _is_body_style_evidence_candidate(
            line,
            bare_line=bare_line,
            title_line=title_line,
            heading_height=heading_height,
            allow_native_title_shape=True,
        )
    ])
    for line in top_lines:
        if _is_next_page_segment_boundary(line, credible_body_styles):
            break
        if _is_body_style_evidence_candidate(
            line,
            bare_line=bare_line,
            title_line=title_line,
            heading_height=heading_height,
            allow_native_title_shape=True,
        ):
            evidence_lines.append(line)
    return _style_baselines(evidence_lines)


def _is_explicit_next_page_boundary(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    return bool(BARE_RE.fullmatch(normalized) or NUMBERED_LINE_RE.match(normalized))


def _is_next_page_segment_boundary(
    line: NativeLineRecord,
    body_styles: tuple[NativeBodyStyleBaseline, ...],
) -> bool:
    if _is_explicit_next_page_boundary(line.text):
        return True
    if not _is_valid_native_title(line.text) or not body_styles:
        return False
    line_style = _dominant_span_style(line)
    if line_style is None:
        return False
    line_is_emphasized = _is_emphasized_style(line_style)
    return all(
        line_style.size - body_style.size
        >= max(1.0, body_style.size * 0.12)
        or (line_is_emphasized and not body_style.emphasized)
        for body_style in body_styles
    )


def _style_baselines(
    lines: list[NativeLineRecord],
) -> tuple[NativeBodyStyleBaseline, ...]:
    weighted_chars: dict[float, int] = {}
    emphasized_chars: dict[float, int] = {}
    line_indexes: dict[float, set[int]] = {}
    for line_index, line in enumerate(lines):
        for style in line.span_styles:
            if style.size <= 0 or style.char_count <= 0:
                continue
            size_bucket = _font_size_bucket(style.size)
            weighted_chars[size_bucket] = weighted_chars.get(size_bucket, 0) + style.char_count
            line_indexes.setdefault(size_bucket, set()).add(line_index)
            if _is_emphasized_style(style):
                emphasized_chars[size_bucket] = (
                    emphasized_chars.get(size_bucket, 0) + style.char_count
                )
    return tuple(
        NativeBodyStyleBaseline(
            size=size,
            emphasized=emphasized_chars.get(size, 0) * 2 > weighted_chars[size],
            char_count=weighted_chars[size],
            line_count=len(line_indexes[size]),
        )
        for size in sorted(weighted_chars)
        if weighted_chars[size] >= SPLIT_BODY_MIN_CHARS
        and len(line_indexes[size]) >= SPLIT_BODY_MIN_LINES
    )


def _font_size_bucket(size: float) -> float:
    return round(math.floor(size * 2.0 + 0.5) / 2.0, 2)


def _is_body_style_evidence_line(
    line: NativeLineRecord,
    *,
    bare_line: NativeLineRecord,
    title_line: NativeLineRecord,
    heading_bottom: float,
    heading_height: float,
) -> bool:
    if line is bare_line or line is title_line:
        return False
    if not 0 <= line.bbox.y0 - heading_bottom <= heading_height * SPLIT_BODY_WINDOW_HEIGHT_RATIO:
        return False
    return _is_body_style_evidence_candidate(
        line,
        bare_line=bare_line,
        title_line=title_line,
        heading_height=heading_height,
    )


def _is_body_style_evidence_candidate(
    line: NativeLineRecord,
    *,
    bare_line: NativeLineRecord,
    title_line: NativeLineRecord,
    heading_height: float,
    allow_native_title_shape: bool = False,
) -> bool:
    if line.bbox.x0 > title_line.bbox.x0 + heading_height * 3.0:
        return False
    if line.bbox.x1 < bare_line.bbox.x0:
        return False
    normalized = unicodedata.normalize("NFKC", line.text).strip()
    compact = re.sub(r"\s+", "", normalized)
    if len(compact) < 4:
        return False
    if BARE_RE.fullmatch(normalized) or HEADING_RE.fullmatch(normalized):
        return False
    return allow_native_title_shape or not _is_valid_native_title(normalized)


def _dominant_span_style(line: NativeLineRecord) -> NativeSpanStyle | None:
    usable = [style for style in line.span_styles if style.size > 0 and style.char_count > 0]
    return max(usable, key=lambda style: (style.char_count, style.size), default=None)


def _is_emphasized_style(style: NativeSpanStyle) -> bool:
    compact_font = re.sub(r"[^a-z0-9]+", "", (style.font or "").lower())
    emphasized_family = any(
        token in compact_font
        for token in ("bold", "black", "heavy", "semibold", "demi", "hei")
    )
    return bool(style.flags & 16) or emphasized_family


def _is_same_heading_line(left: BBox, right: BBox) -> bool:
    if right.x0 < left.x1:
        return False
    left_center = (left.y0 + left.y1) / 2
    right_center = (right.y0 + right.y1) / 2
    height = max(left.y1 - left.y0, right.y1 - right.y0, 1.0)
    gap = right.x0 - left.x1
    return (
        abs(left_center - right_center) <= height * SPLIT_BASELINE_TOLERANCE
        and gap <= height * SPLIT_GAP_HEIGHT_RATIO
    )


def _deduplicate_candidates(
    candidates: list[NativeHeadingCandidate],
) -> list[NativeHeadingCandidate]:
    unique: dict[tuple[object, ...], NativeHeadingCandidate] = {}
    for candidate in candidates:
        key = (
            candidate.page_no,
            candidate.number,
            normalize_heading_text(candidate.title),
            round(candidate.bbox.x0, 2),
            round(candidate.bbox.y0, 2),
            round(candidate.bbox.x1, 2),
            round(candidate.bbox.y1, 2),
        )
        unique.setdefault(key, candidate)
    return list(unique.values())


def _deduplicate_section_title_candidates(
    candidates: list[NativeSectionTitleCandidate],
) -> list[NativeSectionTitleCandidate]:
    unique: dict[tuple[object, ...], NativeSectionTitleCandidate] = {}
    for candidate in candidates:
        key = (
            candidate.page_no,
            normalize_heading_text(candidate.text),
            round(candidate.bbox.x0, 2),
            round(candidate.bbox.y0, 2),
            round(candidate.bbox.x1, 2),
            round(candidate.bbox.y1, 2),
        )
        unique.setdefault(key, candidate)
    return list(unique.values())


def _native_line_records(pdf_page, page_no: int) -> list[NativeLineRecord]:
    result: list[NativeLineRecord] = []
    raw = pdf_page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            chars: list[tuple[str, tuple[float, float, float, float]]] = []
            span_styles: list[NativeSpanStyle] = []
            for span in line.get("spans", []):
                span_char_count = 0
                for item in span.get("chars", []):
                    value = str(item.get("c") or "")
                    bbox = item.get("bbox")
                    if value and isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                        chars.append((value, (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))))
                        if not value.isspace():
                            span_char_count += 1
                if span_char_count:
                    span_styles.append(
                        NativeSpanStyle(
                            size=float(span.get("size") or 0.0),
                            font=str(span.get("font") or ""),
                            flags=int(span.get("flags") or 0),
                            char_count=span_char_count,
                        )
                    )
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
            result.append(
                NativeLineRecord(
                    text=text,
                    bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                    char_boxes=tuple(boxes),
                    span_styles=tuple(span_styles),
                )
            )
    return result


def _native_lines(pdf_page, page_no: int) -> list[tuple[str, BBox, list[CharBox]]]:
    return [
        (line.text, line.bbox, list(line.char_boxes))
        for line in _native_line_records(pdf_page, page_no)
    ]


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
        self._insert_missing_section_titles(document, result)
        return result

    def _insert_missing_section_titles(
        self,
        document: Document,
        result: NativeHeadingRepairResult,
    ) -> None:
        pages = {page.page_no: page for page in document.pages}
        candidates_by_page: dict[int, list[NativeSectionTitleCandidate]] = {}
        for candidate in load_native_section_title_candidates(document.path):
            candidates_by_page.setdefault(candidate.page_no, []).append(candidate)
        catalog_continuation_pages = self._attachment_catalog_continuation_pages(candidates_by_page)

        for page_no, candidates in candidates_by_page.items():
            page = pages.get(page_no)
            if page is None:
                continue
            inserted: list[TextBlock] = []
            for candidate in candidates:
                if self._has_equivalent_text(page, candidate.text):
                    continue
                block_id = f"p{page_no}_native_section_title_{len(inserted) + 1}"
                block_type = candidate.block_type
                if page_no in catalog_continuation_pages and block_type in TITLE_BLOCK_TYPES:
                    block_type = "text"
                inserted.append(
                    TextBlock(
                        block_id=block_id,
                        page_no=page_no,
                        text=candidate.text,
                        bbox=candidate.bbox,
                        char_boxes=list(candidate.char_boxes),
                        block_type=block_type,
                        source="native_section_title_repair",
                        semantic_reasons=["native_section_title_repair"],
                    )
                )
                result.decisions.append(
                    {
                        "action": "inserted",
                        "page_no": page_no,
                        "title": candidate.text,
                        "block_id": block_id,
                        "reason": "missing_ocr_section_title",
                    }
                )
            if inserted:
                page.blocks = sorted(
                    [*page.blocks, *inserted],
                    key=lambda block: (block.bbox.y0, block.bbox.x0, block.block_id),
                )

    @staticmethod
    def _attachment_catalog_continuation_pages(
        candidates_by_page: dict[int, list[NativeSectionTitleCandidate]],
    ) -> set[int]:
        catalog_pages = {
            page_no
            for page_no, candidates in candidates_by_page.items()
            if any(_is_attachment_catalog_entry(candidate.text) for candidate in candidates)
        }
        return {
            page_no
            for page_no, candidates in candidates_by_page.items()
            if page_no - 1 in catalog_pages
            and any(APPENDIX_LABEL_RE.fullmatch(candidate.text) for candidate in candidates)
        }

    @staticmethod
    def _has_equivalent_text(page: Page, text: str) -> bool:
        expected = normalize_heading_text(text)
        return any(
            expected == normalize_heading_text(block.text)
            for block in page.blocks
            if block.text
        )

    @staticmethod
    def _bare_block(page: Page, number: str) -> TextBlock | None:
        matches = [block for block in page.blocks if (match := BARE_RE.fullmatch(unicodedata.normalize("NFKC", block.text).strip())) and match.group("number") == number]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _has_conflicting_title(page: Page, number: str, title: str, bare: TextBlock) -> bool:
        expected_title = normalize_heading_text(title)
        for block in page.blocks:
            if block is bare:
                continue
            match = TOP_LEVEL_LINE_RE.fullmatch(
                unicodedata.normalize("NFKC", block.text).strip()
            )
            if (
                match is not None
                and match.group("number") == number
                and normalize_heading_text(match.group("title")) != expected_title
                and (
                    (block.block_type or "").lower() in TITLE_BLOCK_TYPES
                    or _is_valid_conflict_title(match.group("title"))
                )
            ):
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
