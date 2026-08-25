# Contract Heading Evidence Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate title-only false differences caused by lost native headings, short-title boundary errors, and repeated scan overlays without hiding genuine high-risk clause additions.

**Architecture:** Keep structured OCR authoritative for layout and body text, then run two deterministic post-extraction normalizers before clause splitting. Reuse a cached native heading index in the diff-quality layer as a conservative fallback, and strengthen short top-level title blocks without lowering the global heading threshold.

**Tech Stack:** Python 3.11+, FastAPI backend models, PyMuPDF (`fitz`), Pydantic, existing comparison pipeline, pytest, Ruff.

## Global Constraints

- Keep `/api/compare/*` and `/api/extract/*` response contracts unchanged.
- Do not replace whole-document structured OCR with native text extraction.
- Do not reconstruct missing body paragraphs from native text.
- Do not change matcher scoring, assignment, or semantic reranking.
- Do not add task-ID, filename, party-name, or clause-title special cases.
- Native-text failures must be fail-open and must not fail comparison tasks.
- High-risk heading ADDs remain visible unless exact native heading evidence and matching structure prove coverage.
- Do not commit supplied contracts or any files under `storage/` or `tmp/`.
- Preserve all unrelated worktree changes; stage only the files named by each task.

---

## File Structure


---

### Task 1: Add Native Heading Index and Repair Service

**Files:**
- Create: `backend/app/services/native_heading_repair.py`
- Create: `backend/tests/test_native_heading_repair.py`

**Interfaces:**
- Consumes: `Document`, `Page`, `TextBlock`, `BBox`, and `CharBox` from `app.models`; `Document.path` must point to the PDF being inspected.
- Produces: `NativeHeadingRepairService.repair(document: Document) -> NativeHeadingRepairResult` and `load_native_heading_index(path: str | Path) -> NativeHeadingIndex` for Task 5.

- [ ] **Step 1: Write failing repair and safety tests**

Create `backend/tests/test_native_heading_repair.py`:

```python
from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, Document, Page, TextBlock
from app.services.native_heading_repair import NativeHeadingRepairService, load_native_heading_index


def _write_pdf(path: Path, lines: list[tuple[float, str]]) -> None:
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    for y, text in lines:
        page.insert_text((72, y), text, fontname="china-s", fontsize=12)
    pdf.save(path)
    pdf.close()


def _ocr_document(path: Path, heading: str = "8.", *, title_type: str = "paragraph_title") -> Document:
    return Document(
        filename=path.name,
        path=str(path),
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="heading",
                        page_no=1,
                        text=heading,
                        bbox=BBox(x0=70, y0=82, x1=92, y1=102),
                        block_type=title_type,
                        source="ppocrv5",
                    ),
                    TextBlock(
                        block_id="child",
                        page_no=1,
                        text="8.1 甲方拥有工作成果。",
                        bbox=BBox(x0=72, y0=116, x1=360, y1=138),
                    ),
                ],
            )
        ],
    )


def test_repairs_bare_number_from_exact_native_heading(tmp_path: Path) -> None:
    path = tmp_path / "native.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path)

    result = NativeHeadingRepairService().repair(document)

    heading = document.pages[0].blocks[0]
    assert heading.text == "8. 知识产权"
    assert heading.bbox.x1 > 92
    assert heading.char_boxes
    assert "native_heading_repair" in heading.source
    assert "native_heading_repair:8" in heading.semantic_reasons
    assert result.repaired_count == 1
    assert result.decisions[0]["action"] == "repaired"


def test_does_not_replace_conflicting_ocr_title(tmp_path: Path) -> None:
    path = tmp_path / "conflict.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path, "8. 保密")

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8. 保密"
    assert result.repaired_count == 0


def test_rejects_wrong_number_and_amount_like_native_lines(tmp_path: Path) -> None:
    path = tmp_path / "wrong-or-value.pdf"
    _write_pdf(path, [(96, "9. 知识产权"), (160, "8. 100万元")])
    document = _ocr_document(path)

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8."
    assert result.repaired_count == 0


def test_rejects_ambiguous_native_heading_and_fails_open(tmp_path: Path) -> None:
    ambiguous = tmp_path / "ambiguous.pdf"
    _write_pdf(ambiguous, [(96, "8. 知识产权"), (160, "8. 保密")])
    document = _ocr_document(ambiguous)

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8."
    assert result.repaired_count == 0
    assert any(item["reason"] == "ambiguous_native_heading" for item in result.decisions)

    missing = _ocr_document(tmp_path / "missing.pdf")
    missing_result = NativeHeadingRepairService().repair(missing)
    assert missing.pages[0].blocks[0].text == "8."
    assert missing_result.warnings

    blank_path = tmp_path / "blank.pdf"
    blank_pdf = fitz.open()
    blank_pdf.new_page(width=595, height=842)
    blank_pdf.save(blank_path)
    blank_pdf.close()
    blank = _ocr_document(blank_path)
    blank_result = NativeHeadingRepairService().repair(blank)
    assert blank.pages[0].blocks[0].text == "8."
    assert blank_result.repaired_count == 0


def test_native_heading_index_requires_exact_number_and_title(tmp_path: Path) -> None:
    path = tmp_path / "index.pdf"
    _write_pdf(path, [(96, "17. 合同生效"), (160, "18. 份数")])

    index = load_native_heading_index(path)

    assert index.contains_exact("17", "合同生效", {1})
    assert index.contains_exact("18", "份数", {1})
    assert not index.contains_exact("17", "份数", {1})
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_native_heading_repair.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.services.native_heading_repair'`.

- [ ] **Step 3: Implement native heading extraction, caching, and repair**

Create `backend/app/services/native_heading_repair.py` with these public types and rules:

```python
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
    finally:
        pdf.close()
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
```

- [ ] **Step 4: Run native repair tests to verify GREEN**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_native_heading_repair.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run focused extraction and splitter regression tests**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_compare_integration.py backend/tests/test_layout_analysis.py backend/tests/test_text_cleaning_quality.py -q
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add backend/app/services/native_heading_repair.py backend/tests/test_native_heading_repair.py
git commit -m "feat(extraction): repair OCR headings from native text"
```

---

### Task 2: Filter Repeated Short Scan Overlays

**Files:**
- Create: `backend/app/services/repeated_overlay_filter.py`
- Create: `backend/tests/test_repeated_overlay_filter.py`

**Interfaces:**
- Consumes: extracted `Document` blocks after native heading repair.
- Produces: `RepeatedOverlayFilter.apply(document: Document) -> RepeatedOverlayFilterResult`; confirmed blocks remain in the document but are excluded from clause comparison.

- [ ] **Step 1: Write failing overlay tests**

Create `backend/tests/test_repeated_overlay_filter.py`:

```python
from app.models import BBox, Document, Page, TextBlock
from app.services.repeated_overlay_filter import RepeatedOverlayFilter


def _document(repeated_text: str, *, stable: bool = True) -> Document:
    pages: list[Page] = []
    for page_no in range(1, 11):
        x0 = 500 if stable else 20 + page_no * 35
        blocks = [
            TextBlock(
                block_id=f"body-{page_no}",
                page_no=page_no,
                text=f"第{page_no}页合同正文",
                bbox=BBox(x0=60, y0=100, x1=500, y1=130),
            )
        ]
        if page_no <= 9:
            blocks.append(
                TextBlock(
                    block_id=f"overlay-{page_no}",
                    page_no=page_no,
                    text=repeated_text,
                    bbox=BBox(x0=x0, y0=400, x1=x0 + 40, y1=420),
                    source="ppocrv5",
                )
            )
        pages.append(Page(page_no=page_no, width=595, height=842, blocks=blocks))
    return Document(filename="scan.pdf", path="scan.pdf", page_count=10, pages=pages)


def test_marks_stable_repeated_short_overlay_as_noise() -> None:
    document = _document("黄科")

    result = RepeatedOverlayFilter().apply(document)

    overlays = [block for page in document.pages for block in page.blocks if block.block_id.startswith("overlay-")]
    assert all(block.enter_clause_compare is False for block in overlays)
    assert all(block.flow_role == "noise" for block in overlays)
    assert all("repeated_overlay_filter" in block.source for block in overlays)
    assert result.filtered_block_count == 9


def test_keeps_unstable_or_protected_repeated_text() -> None:
    unstable = _document("黄科", stable=False)
    party_labels = _document("甲方")
    headings = _document("8. 保密")

    assert RepeatedOverlayFilter().apply(unstable).filtered_block_count == 0
    assert RepeatedOverlayFilter().apply(party_labels).filtered_block_count == 0
    assert RepeatedOverlayFilter().apply(headings).filtered_block_count == 0


def test_keeps_single_page_contact_name() -> None:
    document = _document("黄科")
    for page in document.pages[1:]:
        page.blocks = [block for block in page.blocks if not block.block_id.startswith("overlay-")]

    result = RepeatedOverlayFilter().apply(document)

    assert result.filtered_block_count == 0
    assert document.pages[0].blocks[-1].enter_clause_compare is None
```

- [ ] **Step 2: Run tests to verify RED**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_repeated_overlay_filter.py -q
```

Expected: collection fails because `app.services.repeated_overlay_filter` does not exist.

- [ ] **Step 3: Implement the repeated overlay filter**

Create `backend/app/services/repeated_overlay_filter.py`:

```python
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median

from app.models import Document, TextBlock
from app.services.clause_numbering import ClauseNumberParser


PROTECTED_LABEL_RE = re.compile(r"^(?:甲方|乙方|买方|卖方|联系人|电话|传真|邮箱|签字|签章|盖章|日期)[:：]?$")
VALUE_RE = re.compile(r"^(?:\d+(?:\.\d+)?(?:元|万元|%|天|月|年|份|项|台|套)?|\d{4}年)$")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


@dataclass
class RepeatedOverlayFilterResult:
    decisions: list[dict[str, object]] = field(default_factory=list)

    @property
    def filtered_block_count(self) -> int:
        return sum(int(item.get("filtered_block_count", 0)) for item in self.decisions if item.get("action") == "filtered")

    def to_debug_payload(self) -> dict[str, object]:
        return {"filtered_block_count": self.filtered_block_count, "decisions": self.decisions}


class RepeatedOverlayFilter:
    min_pages = 5
    min_page_ratio = 0.80
    min_cluster_ratio = 0.80
    position_tolerance = 0.08

    def __init__(self) -> None:
        self.number_parser = ClauseNumberParser()

    def apply(self, document: Document) -> RepeatedOverlayFilterResult:
        result = RepeatedOverlayFilterResult()
        groups: dict[str, list[tuple[TextBlock, float, float]]] = defaultdict(list)
        for page in document.pages:
            for block in page.blocks:
                key = _compact(block.text)
                if not 2 <= len(key) <= 12 or block.enter_clause_compare is False:
                    continue
                groups[key].append((block, (block.bbox.x0 + block.bbox.x1) / (2 * max(page.width, 1)), (block.bbox.y0 + block.bbox.y1) / (2 * max(page.height, 1))))
        for key, occurrences in groups.items():
            page_counts: dict[int, int] = defaultdict(int)
            for block, _, _ in occurrences:
                page_counts[block.page_no] += 1
            page_ratio = len(page_counts) / max(document.page_count, 1)
            cluster_ratio = self._cluster_ratio(occurrences)
            reason = self._rejection_reason(key, occurrences, page_counts, page_ratio, cluster_ratio)
            if reason:
                if len(page_counts) >= self.min_pages:
                    result.decisions.append({"action": "kept", "text": key, "page_ratio": round(page_ratio, 4), "cluster_ratio": round(cluster_ratio, 4), "reason": reason})
                continue
            for block, _, _ in occurrences:
                block.enter_clause_compare = False
                block.flow_role = "noise"
                block.source = "+".join(part for part in [block.source, "repeated_overlay_filter"] if part)
                block.semantic_reasons = [*block.semantic_reasons, "repeated_overlay_filter"]
            result.decisions.append({"action": "filtered", "text": key, "page_ratio": round(page_ratio, 4), "cluster_ratio": round(cluster_ratio, 4), "filtered_block_count": len(occurrences), "block_ids": [block.block_id for block, _, _ in occurrences]})
        return result

    def _rejection_reason(self, key, occurrences, page_counts, page_ratio, cluster_ratio) -> str:
        if len(page_counts) < self.min_pages or page_ratio < self.min_page_ratio:
            return "insufficient_page_coverage"
        if max(page_counts.values()) > 2:
            return "too_many_occurrences_per_page"
        if cluster_ratio < self.min_cluster_ratio:
            return "unstable_position"
        if PROTECTED_LABEL_RE.fullmatch(key) or VALUE_RE.fullmatch(key):
            return "protected_label_or_value"
        if self.number_parser.parse_line(key) is not None:
            return "numbered_heading_or_clause"
        return ""

    def _cluster_ratio(self, occurrences: list[tuple[TextBlock, float, float]]) -> float:
        xs = [x for _, x, _ in occurrences]
        ys = [y for _, _, y in occurrences]
        center_x = median(xs)
        center_y = median(ys)
        clustered = sum(1 for _, x, y in occurrences if abs(x - center_x) <= self.position_tolerance and abs(y - center_y) <= self.position_tolerance)
        return clustered / max(len(occurrences), 1)
```

- [ ] **Step 4: Run overlay tests and focused splitter tests**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_repeated_overlay_filter.py backend/tests/test_text_cleaning_quality.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/app/services/repeated_overlay_filter.py backend/tests/test_repeated_overlay_filter.py
git commit -m "feat(extraction): filter repeated scan overlays"
```

---

### Task 3: Integrate Post-Extraction Normalizers and Diagnostics

**Files:**
- Modify: `backend/app/services/pipeline_stages.py:150-245`
- Modify: `backend/app/services/compare_debug.py:15-110`
- Modify: `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `NativeHeadingRepairService`, `RepeatedOverlayFilter`, and their `to_debug_payload()` results from Tasks 1-2.
- Produces: repaired extraction documents plus `native_heading_repair` and `repeated_overlay_filter` entries in `CompareTask.debug_artifact_paths`.

- [ ] **Step 1: Write a failing pipeline wiring test**

Add `ExtractionStage` to the existing `pipeline_stages` import in `backend/tests/test_pipeline.py`, then add:

```python
class _SequentialExtractor:
    name = "ppstructure_ocr_hybrid"

    def __init__(self, results: list[ExtractionResult]) -> None:
        self.results = results

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        result = self.results.pop(0)
        result.document.path = str(path)
        result.document.filename = Path(path).name
        return result


def test_extraction_stage_repairs_native_heading_and_writes_normalizer_debug(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    _write_text_pdf(ctx.original_pdf, "8. 知识产权\n8.1 甲方拥有工作成果。")
    _write_text_pdf(ctx.compare_pdf, "8. 知识产权\n8.1 甲方拥有工作成果。")
    original = Document(
        filename="original.pdf",
        path=str(ctx.original_pdf),
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[
            TextBlock(block_id="o8", page_no=1, text="8.", bbox=BBox(x0=70, y0=82, x1=92, y1=102), block_type="paragraph_title"),
            TextBlock(block_id="o81", page_no=1, text="8.1 甲方拥有工作成果。", bbox=BBox(x0=72, y0=116, x1=360, y1=138)),
        ])],
    )
    compare = Document(
        filename="compare.pdf",
        path=str(ctx.compare_pdf),
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[
            TextBlock(block_id="n8", page_no=1, text="8. 知识产权", bbox=BBox(x0=70, y0=82, x1=180, y1=102), block_type="paragraph_title"),
            TextBlock(block_id="n81", page_no=1, text="8.1 甲方拥有工作成果。", bbox=BBox(x0=72, y0=116, x1=360, y1=138)),
        ])],
    )
    extractor = _SequentialExtractor([
        ExtractionResult(document=original, extractor_used="ppstructure_ocr_hybrid"),
        ExtractionResult(document=compare, extractor_used="ppstructure_ocr_hybrid"),
    ])

    ExtractionStage(extractor=extractor, artifact_store=LocalArtifactStore(settings)).execute(ctx)

    assert ctx.original_extraction is not None
    assert ctx.original_extraction.document.pages[0].blocks[0].text == "8. 知识产权"
    assert Path(ctx.task.debug_artifact_paths["native_heading_repair"]).exists()
    assert Path(ctx.task.debug_artifact_paths["repeated_overlay_filter"]).exists()
    assert ctx.task.metrics["native_heading_repair"]["original"] == 1
    assert ctx.task.metrics["repeated_overlay_filter"]["compare"] == 0
```

- [ ] **Step 2: Run the test to verify RED**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_pipeline.py::test_extraction_stage_repairs_native_heading_and_writes_normalizer_debug -q
```

Expected: FAIL because `ExtractionStage` does not run either normalizer and debug paths are absent.

- [ ] **Step 3: Add debug writer methods**

Add to `CompareDebugWriter` in `backend/app/services/compare_debug.py`:

```python
    def write_native_heading_repair(self, task_id: str, original: dict[str, Any], compare: dict[str, Any]) -> str:
        return str(self._write_json(task_id, "native_heading_repair.json", {"original": original, "compare": compare}))

    def write_repeated_overlay_filter(self, task_id: str, original: dict[str, Any], compare: dict[str, Any]) -> str:
        return str(self._write_json(task_id, "repeated_overlay_filter.json", {"original": original, "compare": compare}))
```

- [ ] **Step 4: Wire services into `ExtractionStage`**

Add imports:

```python
from app.services.native_heading_repair import NativeHeadingRepairService
from app.services.repeated_overlay_filter import RepeatedOverlayFilter
```

Extend `ExtractionStage.__init__`:

```python
        native_heading_repair: NativeHeadingRepairService | None = None,
        repeated_overlay_filter: RepeatedOverlayFilter | None = None,
```

Initialize them:

```python
        self.native_heading_repair = native_heading_repair or NativeHeadingRepairService()
        self.repeated_overlay_filter = repeated_overlay_filter or RepeatedOverlayFilter()
```

Immediately after `_align_structured_extractions(...)` and before `_ensure_profile(...)`, add:

```python
        original_heading_result = self.native_heading_repair.repair(original_extraction.document)
        compare_heading_result = self.native_heading_repair.repair(compare_extraction.document)
        original_extraction.warnings.extend(original_heading_result.warnings)
        compare_extraction.warnings.extend(compare_heading_result.warnings)
        original_overlay_result = self.repeated_overlay_filter.apply(original_extraction.document)
        compare_overlay_result = self.repeated_overlay_filter.apply(compare_extraction.document)
        _write_debug_artifact(
            task,
            "native_heading_repair",
            lambda: self.debug_writer.write_native_heading_repair(
                task.task_id,
                original_heading_result.to_debug_payload(),
                compare_heading_result.to_debug_payload(),
            ),
        )
        _write_debug_artifact(
            task,
            "repeated_overlay_filter",
            lambda: self.debug_writer.write_repeated_overlay_filter(
                task.task_id,
                original_overlay_result.to_debug_payload(),
                compare_overlay_result.to_debug_payload(),
            ),
        )
        task.metrics["native_heading_repair"] = {
            "original": original_heading_result.repaired_count,
            "compare": compare_heading_result.repaired_count,
        }
        task.metrics["repeated_overlay_filter"] = {
            "original": original_overlay_result.filtered_block_count,
            "compare": compare_overlay_result.filtered_block_count,
        }
        _emit_progress(ctx, 33, self.name, "extraction_evidence_normalization_done")
```

- [ ] **Step 5: Run pipeline and service tests**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_pipeline.py backend/tests/test_compare_integration.py backend/tests/test_native_heading_repair.py backend/tests/test_repeated_overlay_filter.py -q
```

Expected: all tests pass, including strict structured extraction tests.

- [ ] **Step 6: Commit Task 3**

```bash
git add backend/app/services/pipeline_stages.py backend/app/services/compare_debug.py backend/tests/test_pipeline.py
git commit -m "feat(pipeline): apply extraction evidence normalization"
```

---

### Task 4: Keep Short Numbered Title Blocks as Clauses

**Files:**
- Modify: `backend/app/services/clause_heading.py:70-155`
- Modify: `backend/tests/test_text_cleaning_quality.py`

**Interfaces:**
- Consumes: existing `HeadingCandidate` inputs and layout block type.
- Produces: a `strong_title_block` candidate signal; no public API changes.

- [ ] **Step 1: Write failing positive and negative splitter tests**

Add to `backend/tests/test_text_cleaning_quality.py`:

```python
def test_clause_splitter_keeps_short_top_level_title_blocks_independent() -> None:
    document = Document(
        filename="short-headings.pdf",
        path="short-headings.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[
            TextBlock(block_id="c124", page_no=1, text="12.4 双方协商解决。", bbox=BBox(x0=70, y0=80, x1=500, y1=105)),
            TextBlock(block_id="h13", page_no=1, text="13. 索赔", bbox=BBox(x0=70, y0=130, x1=150, y1=154), block_type="paragraph_title"),
            TextBlock(block_id="c131", page_no=1, text="13.1 甲方有权提出索赔。", bbox=BBox(x0=72, y0=170, x1=500, y1=195)),
            TextBlock(block_id="h17", page_no=1, text="17. 合同生效", bbox=BBox(x0=70, y0=230, x1=180, y1=254), block_type="paragraph_title"),
            TextBlock(block_id="c171", page_no=1, text="(1)双方签字盖章。", bbox=BBox(x0=92, y0=270, x1=500, y1=295)),
            TextBlock(block_id="h18", page_no=1, text="18. 份数", bbox=BBox(x0=70, y0=330, x1=150, y1=354), block_type="paragraph_title"),
            TextBlock(block_id="c18", page_no=1, text="本合同一式伍份。", bbox=BBox(x0=92, y0=370, x1=500, y1=395)),
        ])],
    )

    clauses = ClauseSplitter().split(document, "N")

    assert "13" in [item.clause_no for item in clauses]
    assert "18" in [item.clause_no for item in clauses]
    assert next(item for item in clauses if item.clause_no == "13").title == "索赔"
    assert next(item for item in clauses if item.clause_no == "18").title == "份数"


def test_clause_splitter_keeps_short_numeric_values_outside_title_blocks() -> None:
    document = Document(
        filename="values.pdf",
        path="values.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[
            TextBlock(block_id="h17", page_no=1, text="17. 合同生效", bbox=BBox(x0=70, y0=80, x1=180, y1=104), block_type="paragraph_title"),
            TextBlock(block_id="v1", page_no=1, text="(2)1", bbox=BBox(x0=92, y0=120, x1=130, y1=144)),
            TextBlock(block_id="v2", page_no=1, text="18份", bbox=BBox(x0=92, y0=160, x1=140, y1=184)),
        ])],
    )

    clauses = ClauseSplitter().split(document, "N")

    assert [item.clause_no for item in clauses] == ["17"]
    assert "18份" in clauses[0].text
```

- [ ] **Step 2: Run tests to verify RED**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_text_cleaning_quality.py::test_clause_splitter_keeps_short_top_level_title_blocks_independent backend/tests/test_text_cleaning_quality.py::test_clause_splitter_keeps_short_numeric_values_outside_title_blocks -q
```

Expected: the first test fails because clauses 13 and 18 are merged into previous clauses; the negative test passes.

- [ ] **Step 3: Add the strong title-block signal without lowering thresholds**

In `ClauseHeadingDetector.candidate`, after `compact_title` is calculated, add:

```python
        strong_title_block = (
            block_type in {"paragraph_title", "doc_title", "title"}
            and bool(self.weak_numeric_marker_pattern.fullmatch(clause_no or ""))
            and 2 <= len(compact_title) <= 12
        )
        if strong_title_block:
            signals.append("strong_title_block")
```

Replace the weak-marker cap block with:

```python
        if self.is_weak_numeric_marker(text, marker) and not strong_title_block:
            score = min(score, self.weak_heading_review_score)
            risk_flags.append("WEAK_NUMERIC_MARKER")
```

Do not change `heading_accept_score`, quantity detection, date detection, or table filtering.

- [ ] **Step 4: Run full splitter coverage**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_text_cleaning_quality.py backend/tests/test_clause_split_diagnostics.py backend/tests/test_clause_split_golden.py backend/tests/test_clause_split_settings.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add backend/app/services/clause_heading.py backend/tests/test_text_cleaning_quality.py
git commit -m "fix(splitter): keep short title blocks as clauses"
```

---

### Task 5: Add Native-Evidence Diff-Quality Fallback

**Files:**
- Modify: `backend/app/services/diff/boundary_coverage.py:70-175, 500-565`
- Modify: `backend/tests/test_text_cleaning_quality.py`

**Interfaces:**
- Consumes: `load_native_heading_index()` from Task 1 and existing `BoundaryCoverageContext` documents/clauses.
- Produces: suppression reasons `heading_add_covered_by_native_heading` and `native_heading_title_only_covered`.

- [ ] **Step 1: Write failing high-risk ADD and MODIFY tests**

Add `from pathlib import Path` and `import fitz` to `backend/tests/test_text_cleaning_quality.py`, then add:

```python
def _write_quality_heading_pdf(path: Path, heading: str) -> None:
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 96), heading, fontname="china-s", fontsize=12)
    pdf.save(path)
    pdf.close()


def test_diff_quality_suppresses_high_risk_heading_add_with_exact_native_evidence(tmp_path: Path) -> None:
    path = tmp_path / "original.pdf"
    _write_quality_heading_pdf(path, "8. 知识产权")
    original_document = _quality_document(1, "8.\n8.1 甲方拥有工作成果。")
    original_document.path = str(path)
    original_child = _quality_clause("OC081", "8.1 甲方拥有工作成果。", order_index=2)
    original_child.clause_no = "8.1"
    compare_heading = _quality_clause("NC080", "8. 知识产权", side_prefix="N", order_index=1, split_flags=["READING_ORDER_REPAIRED"])
    compare_heading.clause_no = "8"
    compare_heading.title = "知识产权"
    compare_child = _quality_clause("NC081", "8.1 甲方拥有工作成果。", side_prefix="N", order_index=2)
    compare_child.clause_no = "8.1"
    diff = DiffItem(
        diff_id="D_NATIVE_ADD",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC080",
        clause_no="8",
        title="知识产权",
        compare_text="8. 知识产权",
        compare_snippet="8. 知识产权",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=compare_heading.bboxes,
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_child],
        compare_clauses=[compare_heading, compare_child],
        original_document=original_document,
    )

    assert result.diffs == []
    assert any(item.detail.get("reason") == "heading_add_covered_by_native_heading" for item in result.decisions)


def test_diff_quality_keeps_high_risk_heading_add_without_exact_native_evidence(tmp_path: Path) -> None:
    path = tmp_path / "original.pdf"
    _write_quality_heading_pdf(path, "8. 保密")
    original_document = _quality_document(1, "8.\n8.1 甲方拥有工作成果。")
    original_document.path = str(path)
    compare_heading = _quality_clause("NC080", "8. 知识产权", side_prefix="N", split_flags=["READING_ORDER_REPAIRED"])
    compare_heading.clause_no = "8"
    compare_heading.title = "知识产权"
    diff = DiffItem(
        diff_id="D_NATIVE_ADD_KEEP",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC080",
        clause_no="8",
        title="知识产权",
        compare_text="8. 知识产权",
        compare_snippet="8. 知识产权",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=compare_heading.bboxes,
    )

    result = DiffQualityProcessor().process([diff], compare_clauses=[compare_heading], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D_NATIVE_ADD_KEEP"]


def test_diff_quality_suppresses_title_only_modify_with_native_evidence(tmp_path: Path) -> None:
    path = tmp_path / "original.pdf"
    _write_quality_heading_pdf(path, "17. 合同生效")
    original_document = _quality_document(1, "17.\n本合同在双方签章后生效。")
    original_document.path = str(path)
    original_clause = _quality_clause("OC170", "17.\n本合同在双方签章后生效。")
    original_clause.clause_no = "17"
    compare_clause = _quality_clause("NC170", "17. 合同生效\n本合同在双方签章后生效。", side_prefix="N")
    compare_clause.clause_no = "17"
    compare_clause.title = "合同生效"
    diff = DiffItem(
        diff_id="D_NATIVE_MODIFY",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC170",
        compare_clause_id="NC170",
        clause_no="17",
        title="合同生效",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="",
        compare_snippet="合同生效",
        match_score_details={"alignment": {"body_similarity": 0.98}, "business_token_mismatch": 0.0},
        review_flags=["READING_ORDER_RISK"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
        original_document=original_document,
    )

    assert result.diffs == []
    assert any(item.detail.get("reason") == "native_heading_title_only_covered" for item in result.decisions)


def test_diff_quality_keeps_native_heading_add_when_clause_contains_new_body(tmp_path: Path) -> None:
    path = tmp_path / "original.pdf"
    _write_quality_heading_pdf(path, "8. 知识产权")
    original_document = _quality_document(1, "8.\n8.1 甲方拥有工作成果。")
    original_document.path = str(path)
    original_child = _quality_clause("OC081", "8.1 甲方拥有工作成果。", order_index=2)
    original_child.clause_no = "8.1"
    compare_heading = _quality_clause("NC080", "8. 知识产权\n新增许可限制。", side_prefix="N", order_index=1, split_flags=["READING_ORDER_REPAIRED"])
    compare_heading.clause_no = "8"
    compare_heading.title = "知识产权"
    compare_child = _quality_clause("NC081", "8.1 甲方拥有工作成果。", side_prefix="N", order_index=2)
    compare_child.clause_no = "8.1"
    diff = DiffItem(
        diff_id="D_NATIVE_BODY_ADD",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC080",
        clause_no="8",
        title="知识产权",
        compare_text=compare_heading.text,
        compare_snippet=compare_heading.text,
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=compare_heading.bboxes,
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_child],
        compare_clauses=[compare_heading, compare_child],
        original_document=original_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D_NATIVE_BODY_ADD"]
```

- [ ] **Step 2: Run new tests to verify RED**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_text_cleaning_quality.py -k "native_evidence" -q
```

Expected: suppression assertions fail because the boundary filter does not consult native headings.

- [ ] **Step 3: Cache native indexes in boundary context**

Import the index in `boundary_coverage.py`:

```python
from app.services.native_heading_repair import NativeHeadingIndex, load_native_heading_index, normalize_heading_text
```

Add this field and method to `BoundaryCoverageContext`:

```python
    _native_indexes: dict[str, NativeHeadingIndex] = field(default_factory=dict, init=False, repr=False)

    def native_heading_index(self, document: Document | None) -> NativeHeadingIndex:
        if document is None or not document.path:
            return NativeHeadingIndex()
        if document.path not in self._native_indexes:
            self._native_indexes[document.path] = load_native_heading_index(document.path)
        return self._native_indexes[document.path]
```

- [ ] **Step 4: Add exact native heading helpers and suppression reason**

In `_suppression_reason`, before the existing heading-number coverage checks, add:

```python
        if self._heading_add_covered_by_native_heading(diff, context):
            return "heading_add_covered_by_native_heading"
        if self._native_heading_title_only_modify_covered(diff, context):
            return "native_heading_title_only_covered"
```

Add methods to `ClauseBoundaryCoverageFilter`:

```python
    def _native_heading_confirms(
        self,
        document: Document | None,
        pages: set[int],
        number: str,
        title: str,
        context: BoundaryCoverageContext,
    ) -> bool:
        if not pages or not number or not title:
            return False
        candidate_pages = {page + offset for page in pages for offset in (-1, 0, 1) if page + offset > 0}
        return context.native_heading_index(document).contains_exact(number, title, candidate_pages)

    def _native_heading_title_only_modify_covered(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        original_clause = self._clause_by_id(context.original_clauses, diff.original_clause_id)
        compare_clause = self._clause_by_id(context.compare_clauses, diff.compare_clause_id)
        if original_clause is None or compare_clause is None:
            return False
        if not original_clause.clause_no or original_clause.clause_no != compare_clause.clause_no:
            return False
        details = diff.match_score_details or {}
        alignment = details.get("alignment") if isinstance(details.get("alignment"), dict) else {}
        body_similarity = float(alignment.get("body_similarity", details.get("body_similarity", 0.0)) or 0.0)
        if body_similarity < 0.90 or float(details.get("business_token_mismatch", 0.0) or 0.0) > 0.0:
            return False
        changed = (diff.compare_snippet or "").strip()
        title = compare_clause.title or diff.title
        changed_key = normalize_heading_text(changed)
        allowed = {normalize_heading_text(title), normalize_heading_text(f"{compare_clause.clause_no}{title}")}
        if changed_key not in allowed or _contains_protected_value(changed):
            return False
        original_changed = (diff.original_snippet or "").strip()
        if original_changed and not re.fullmatch(r"\d{1,2}\s*[.．、]?", unicodedata.normalize("NFKC", original_changed)):
            return False
        pages = self._candidate_pages_for_modify_side(diff, "original") or set(original_clause.page_numbers)
        return self._native_heading_confirms(context.original_document, pages, compare_clause.clause_no, title, context)
```

- [ ] **Step 5: Add a dedicated native-backed high-risk ADD predicate**

Keep `_heading_add_covered_by_opposite_numbering()` unchanged so its current non-high-risk behavior and high-risk guard remain intact. Add this separate method to `ClauseBoundaryCoverageFilter`:

```python
    def _heading_add_covered_by_native_heading(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "ADD":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection({"READING_ORDER_REPAIRED", "READING_ORDER_RISK", "POSSIBLE_SEGMENTATION_DRIFT"}):
            return False
        changed = diff.compare_text or diff.compare_snippet
        changed_key = normalize_for_coverage(changed)
        if not _safe_for_heading_number_coverage(changed, changed_key):
            return False
        if not _contains_high_risk_heading_term(changed):
            return False
        compare_clause = self._clause_by_id(context.compare_clauses, diff.compare_clause_id)
        if compare_clause is None:
            return False
        parent_no = self._heading_parent_number(compare_clause, context.compare_index)
        if not parent_no or not self._is_parent_heading_only_clause(compare_clause, changed, parent_no):
            return False
        if not (
            self._clauses_have_child_for_parent(context.original_clauses, parent_no)
            and self._clauses_have_child_for_parent(context.compare_clauses, parent_no)
        ):
            return False
        pages = self._candidate_pages_for_diff(diff, context) or set(compare_clause.page_numbers)
        if not self._native_heading_confirms(
            context.original_document,
            pages,
            parent_no,
            compare_clause.title,
            context,
        ):
            return False
        return self._opposite_pages_have_bare_parent_and_child(
            context.original_document,
            pages,
            parent_no,
        )
```

- [ ] **Step 6: Run focused and full diff-quality tests**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_text_cleaning_quality.py backend/tests/test_task_434_visual_regressions.py -q
```

Expected: new tests pass; existing tests that retain uncorroborated critical headings continue to pass.

- [ ] **Step 7: Commit Task 5**

```bash
git add backend/tests/test_text_cleaning_quality.py
git add -p backend/app/services/diff/boundary_coverage.py
git diff --cached -- backend/app/services/diff/boundary_coverage.py backend/tests/test_text_cleaning_quality.py
git commit -m "fix(diff): corroborate title changes with native text"
```

The staged diff must contain only Task 5 hunks. Leave all pre-existing unstaged
`boundary_coverage.py` edits in the worktree.

---

### Task 6: Add Generated End-to-End Regression and Verify the Real Task

**Files:**
- Create: `backend/tests/test_contract_heading_evidence_repair.py`

**Interfaces:**
- Consumes: the two normalizers, `ClauseSplitter`, `ClausePair`, and `build_diffs`.
- Produces: one deterministic regression covering all nine headings without external OCR or sensitive fixtures.

- [ ] **Step 1: Add generated end-to-end regression**

Create `backend/tests/test_contract_heading_evidence_repair.py`:

```python
from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, ClausePair, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.diff.builder import build_diffs
from app.services.native_heading_repair import NativeHeadingRepairService
from app.services.repeated_overlay_filter import RepeatedOverlayFilter


HEADINGS = {
    1: [("2", "服务内容")],
    2: [("8", "知识产权"), ("9", "保密")],
    3: [("11", "合同变更、终止"), ("12", "不可抗力"), ("13", "索赔"), ("14", "违约责任")],
    4: [("17", "合同生效"), ("18", "份数")],
}
TARGET_NUMBERS = {number for headings in HEADINGS.values() for number, _ in headings}


def _write_native_pdf(path: Path) -> None:
    pdf = fitz.open()
    for page_no in range(1, 6):
        page = pdf.new_page(width=595, height=842)
        for index, (number, title) in enumerate(HEADINGS.get(page_no, [])):
            y = 96 + index * 140
            page.insert_text((72, y), f"{number}. {title}", fontname="china-s", fontsize=12)
    pdf.save(path)
    pdf.close()


def _ocr_document(path: Path, *, complete_titles: bool, overlay: bool) -> Document:
    pages: list[Page] = []
    for page_no in range(1, 6):
        blocks: list[TextBlock] = []
        for index, (number, title) in enumerate(HEADINGS.get(page_no, [])):
            y0 = 82 + index * 140
            heading_text = f"{number}. {title}" if complete_titles else f"{number}."
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}-h{number}",
                    page_no=page_no,
                    text=heading_text,
                    bbox=BBox(x0=70, y0=y0, x1=190 if complete_titles else 92, y1=y0 + 22),
                    block_type="paragraph_title",
                    source="ppocrv5",
                )
            )
            body_text = "本合同一式伍份。" if number == "18" else f"{number}.1 双方按本条约定履行义务。"
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}-b{number}",
                    page_no=page_no,
                    text=body_text,
                    bbox=BBox(x0=92, y0=y0 + 34, x1=500, y1=y0 + 58),
                )
            )
        if overlay:
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}-overlay",
                    page_no=page_no,
                    text="黄科",
                    bbox=BBox(x0=500, y0=780, x1=540, y1=800),
                    source="ppocrv5",
                )
            )
        pages.append(Page(page_no=page_no, width=595, height=842, blocks=blocks))
    return Document(filename=path.name, path=str(path), page_count=5, pages=pages)


def test_generated_heading_evidence_repair_removes_all_title_only_false_diffs(tmp_path: Path) -> None:
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    _write_native_pdf(original_path)
    _write_native_pdf(compare_path)
    original = _ocr_document(original_path, complete_titles=False, overlay=False)
    compare = _ocr_document(compare_path, complete_titles=True, overlay=True)

    heading_result = NativeHeadingRepairService().repair(original)
    overlay_result = RepeatedOverlayFilter().apply(compare)
    original_clauses = ClauseSplitter().split(original, "O")
    compare_clauses = ClauseSplitter().split(compare, "N")

    assert heading_result.repaired_count == 9
    assert overlay_result.filtered_block_count == 5
    original_by_number = {item.clause_no: item for item in original_clauses if item.clause_no in TARGET_NUMBERS}
    compare_by_number = {item.clause_no: item for item in compare_clauses if item.clause_no in TARGET_NUMBERS}
    assert set(original_by_number) == TARGET_NUMBERS
    assert set(compare_by_number) == TARGET_NUMBERS
    assert original_by_number["13"].title == compare_by_number["13"].title == "索赔"
    assert original_by_number["18"].title == compare_by_number["18"].title == "份数"

    pairs = [
        ClausePair(
            original=original_by_number[number],
            compare=compare_by_number[number],
            score=100.0,
            match_method="same_clause_no_weighted",
            score_details={"alignment": {"body_similarity": 1.0}},
        )
        for number in sorted(TARGET_NUMBERS, key=int)
    ]

    assert build_diffs(pairs) == []
    assert all("黄科" not in clause.text for clause in compare_clauses)
    assert all(
        block.enter_clause_compare is False
        for page in compare.pages
        for block in page.blocks
        if block.text == "黄科"
    )
```

- [ ] **Step 2: Run the generated regression**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_contract_heading_evidence_repair.py -q
```

Expected: PASS with all nine parent headings and no generated title-only diffs.

- [ ] **Step 3: Run all focused tests**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_native_heading_repair.py backend/tests/test_repeated_overlay_filter.py backend/tests/test_contract_heading_evidence_repair.py backend/tests/test_pipeline.py backend/tests/test_text_cleaning_quality.py backend/tests/test_task_434_visual_regressions.py -q
```

Expected: all focused tests pass.

- [ ] **Step 4: Run static verification**

```bash
env -u VIRTUAL_ENV uv run ruff check backend/app/services/native_heading_repair.py backend/app/services/repeated_overlay_filter.py backend/app/services/pipeline_stages.py backend/app/services/compare_debug.py backend/app/services/clause_heading.py backend/app/services/diff/boundary_coverage.py backend/tests/test_native_heading_repair.py backend/tests/test_repeated_overlay_filter.py backend/tests/test_contract_heading_evidence_repair.py backend/tests/test_pipeline.py backend/tests/test_text_cleaning_quality.py
env -u VIRTUAL_ENV uv run python -m compileall backend/app backend/tests
```

Expected: Ruff reports no errors and compileall exits zero.

- [ ] **Step 5: Run the complete backend test suite**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests -q
```

Expected: the complete backend suite passes.

- [ ] **Step 6: Commit generated regression**

```bash
git add backend/tests/test_contract_heading_evidence_repair.py
git commit -m "test(compare): cover heading evidence repair pipeline"
```

- [ ] **Step 7: Reprocess the affected task as local acceptance**

From the repository root, run:

```bash
env -u VIRTUAL_ENV PYTHONPATH=backend uv run python -c 'import json; from pathlib import Path; from app.models import CompareOptions; from app.application.compare_tasks import CompareTaskApplication; task_dir=Path("storage/tasks/cd3da5ca-e867-49db-aaf9-c35eb245639e"); payload=json.loads((task_dir / "job.json").read_text(encoding="utf-8"))["payload"]; CompareTaskApplication()._run_compare_task(original_path=Path(payload["original_path"]), compare_path=Path(payload["compare_path"]), task_id=payload["task_id"], original_filename=payload.get("original_filename"), compare_filename=payload.get("compare_filename"), compare_options=CompareOptions.model_validate(payload.get("compare_options") or {})); print("rerun_done")'
```

Expected: task completes. If remote semantic reranking is unavailable, record the warning but continue only if the deterministic pipeline completes.

- [ ] **Step 8: Verify acceptance output without modifying it**

Run:

```bash
jq '[.diffs[] | select(((.title // "") + " " + (.compare_snippet // "")) | test("服务内容|知识产权|保密|合同变更、终止|不可抗力|索赔|违约责任|合同生效|份数")) | {diff_id,diff_type,title,original_snippet,compare_snippet}]' storage/tasks/cd3da5ca-e867-49db-aaf9-c35eb245639e/task.json
jq '[.[] | select(.clause_no == "13" or .clause_no == "18") | {clause_id,clause_no,title,section_path,text_preview}]' storage/tasks/cd3da5ca-e867-49db-aaf9-c35eb245639e/debug/clauses_original.json
jq '[.[] | select(.clause_no == "13" or .clause_no == "18") | {clause_id,clause_no,title,section_path,text_preview}]' storage/tasks/cd3da5ca-e867-49db-aaf9-c35eb245639e/debug/clauses_compare.json
```

Expected:

- no title-only ADD/MODIFY remains for the nine target parent headings;
- Original and Compare each contain independent parent clauses 13 and 18;
- no target clause text contains repeated `黄科` overlay content;
- genuine body differences, if any, remain visible and are not manually deleted from task output.

- [ ] **Step 9: Inspect final worktree scope**

```bash
git status --short
git log -6 --oneline
```

Expected: only known pre-existing unrelated changes and generated untracked artifacts remain. The implementation is represented by six focused commits, and no `storage/` or `tmp/` files are staged.
