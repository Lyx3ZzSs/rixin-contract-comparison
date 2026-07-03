# Task 4736 Visual Diff Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix task `4736c006-cb0e-4ef0-8b39-3594ac73aced` false positives and false negatives by separating body/key-field diffs from signing, seal, handwriting, and scanner artifacts, then verify by rerunning and rendering the PDFs.

**Architecture:** Add conservative, source-backed validators around the existing pipeline rather than replacing OCR or the matcher. Native PDF text becomes an optional equality/field-recovery source; signing/seal/scan artifacts are classified with page type, bbox, and visual evidence; quality filtering removes low-value OCR noise while preserving protected values.

**Tech Stack:** Python 3, FastAPI backend models, Pydantic, PyMuPDF (`fitz`), existing compare pipeline services, pytest, Vite/React audit grouping.

---

## Scope Check

The design covers one subsystem: contract comparison accuracy for visually reviewed PDF tasks. It touches extraction-adjacent indexing, document preparation, pre-clause field diffs, quality filtering, seal output, frontend grouping, and verification scripts. These changes are coupled by one output contract, so they belong in one implementation plan.

## File Structure

- Create `backend/app/services/native_text_index.py`
  - Extract and normalize native PDF text per page.
  - Provide page-window coverage and equality helpers.
- Create `backend/app/services/signing_region.py`
  - Detect signing-region candidates using page type, layout, keyword anchors, and visual evidence.
  - Prevent global keyword-only signing classification.
- Create `backend/app/services/key_field_compare.py`
  - Build focused metadata diffs for contract identity fields and signing dates.
- Create `backend/scripts/task_4736_compare_summary.py`
  - Export before/after task diff summaries used by verification.
- Create `backend/scripts/render_task_pages.py`
  - Render selected PDF pages and side-by-side inspection images.
- Modify `backend/app/services/pipeline_stages.py`
  - Wire key-field diffs into `PreClauseDiffStage`.
- Modify `backend/app/services/document_preparation.py`
  - Mark signing/scan/footer roles without polluting body clauses.
- Modify `backend/app/services/header_footer_compare.py`
  - Avoid emitting low-information footer artifacts as meaningful header/footer diffs.
- Modify `backend/app/services/cover/patterns.py`
  - Skip scanner watermark and edge fragments in cover extra text.
- Modify `backend/app/services/diff_quality.py`
  - Invoke native-text equality, visual noise suppression, and signing reclassification.
- Modify `backend/app/services/diff/boundary_coverage.py`
  - Broaden heading coverage only where page text/native text proves both sides contain the heading.
- Modify `backend/app/services/seal_comparator.py`
  - Prefer stable seal/signature region descriptions over noisy OCR strings.
- Modify `frontend/src/pages/ResultPage.tsx`
  - Group `VISUAL_SIGNING_ARTIFACT`, `SCAN_MARK_REVIEW`, and `SIGNING_DATE_FIELD_CHANGE` outside the main body list.
  - Add a stable `data-audit-group` test hook to audit group sections.
- Test files:
  - `backend/tests/test_native_text_index.py`
  - `backend/tests/test_text_cleaning_quality.py`
  - `backend/tests/test_cover_metadata.py`
  - `backend/tests/test_header_footer_compare.py`
  - `backend/tests/test_seal_comparator.py`
  - `backend/tests/test_pipeline.py`
  - `frontend/src/pages/ResultPage.test.tsx`

### Task 1: Native PDF Text Index And Equality Guard

**Files:**
- Create: `backend/app/services/native_text_index.py`
- Modify: `backend/app/services/diff_quality.py`
- Test: `backend/tests/test_native_text_index.py`
- Test: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Write native text index tests**

Add `backend/tests/test_native_text_index.py`:

```python
from __future__ import annotations

from pathlib import Path

import fitz

from app.models import Document, Page
from app.services.native_text_index import NativeTextPageIndex, normalize_native_text_key


def _write_pdf(path: Path, pages: list[str]) -> None:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_normalize_native_text_key_unifies_bracket_forms() -> None:
    assert normalize_native_text_key("〔2006〕34号") == normalize_native_text_key("(2006)34号")


def test_native_text_index_extracts_page_text_by_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _write_pdf(pdf_path, ["第一页 〔2006〕34号", "第二页 服务期限"])
    document = Document(filename="sample.pdf", path=str(pdf_path), page_count=2, pages=[
        Page(page_no=1, width=595, height=842),
        Page(page_no=2, width=595, height=842),
    ])

    index = NativeTextPageIndex.from_document(document)

    assert index.contains(1, "〔2006〕34号")
    assert index.contains(1, "(2006)34号")
    assert index.contains_any_page("服务期限", pages={2})
    assert not index.contains_any_page("服务期限", pages={1})
```

- [ ] **Step 2: Run the native text index tests and verify they fail**

Run:

```bash
uv run pytest backend/tests/test_native_text_index.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'app.services.native_text_index'`.

- [ ] **Step 3: Implement native text index**

Create `backend/app/services/native_text_index.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import unicodedata

from app.models import Document


_PUNCTUATION_PATTERN = re.compile(r"[\s，。；：、”“‘’《》【】\[\]{}!！?？;:'\"`~～·•,.\-—_＿]+")


def normalize_native_text_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.translate(str.maketrans({
        "〔": "(",
        "〕": ")",
        "（": "(",
        "）": ")",
        "【": "(",
        "】": ")",
    }))
    normalized = re.sub(r"(?<=\d)[()](?=\d{1,4})", "", normalized)
    return _PUNCTUATION_PATTERN.sub("", normalized).lower()


@dataclass(frozen=True)
class NativePageText:
    page_no: int
    text: str
    text_key: str
    line_keys: tuple[str, ...]


@dataclass(frozen=True)
class NativeTextPageIndex:
    pages: tuple[NativePageText, ...]

    @classmethod
    def from_document(cls, document: Document | None) -> "NativeTextPageIndex":
        if document is None:
            return cls(())
        pdf_path = _existing_pdf_path(document.path)
        if pdf_path is None:
            return cls(())
        page_texts = _extract_native_text(str(pdf_path))
        pages = []
        for page_no, text in enumerate(page_texts, start=1):
            lines = tuple(
                key for key in (normalize_native_text_key(line) for line in text.splitlines()) if key
            )
            pages.append(NativePageText(
                page_no=page_no,
                text=text,
                text_key=normalize_native_text_key(text),
                line_keys=lines,
            ))
        return cls(tuple(pages))

    def page(self, page_no: int) -> NativePageText | None:
        for page in self.pages:
            if page.page_no == page_no:
                return page
        return None

    def contains(self, page_no: int, text: str) -> bool:
        page = self.page(page_no)
        if page is None:
            return False
        key = normalize_native_text_key(text)
        return bool(key and key in page.text_key)

    def contains_any_page(self, text: str, pages: set[int] | None = None) -> bool:
        key = normalize_native_text_key(text)
        if not key:
            return False
        for page in self.pages:
            if pages is not None and page.page_no not in pages:
                continue
            if key in page.text_key:
                return True
        return False

    def window_contains(self, text: str, page_numbers: set[int], radius: int = 1) -> bool:
        search_pages = {page_no + offset for page_no in page_numbers for offset in range(-radius, radius + 1)}
        return self.contains_any_page(text, pages={page_no for page_no in search_pages if page_no > 0})


def _existing_pdf_path(path: str) -> Path | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if candidate.exists() and candidate.suffix.lower() == ".pdf":
        return candidate
    return None


@lru_cache(maxsize=64)
def _extract_native_text(path: str) -> tuple[str, ...]:
    try:
        import fitz

        with fitz.open(path) as pdf:
            return tuple(page.get_text() for page in pdf)
    except Exception:
        return ()
```

- [ ] **Step 4: Run native text index tests and verify they pass**

Run:

```bash
uv run pytest backend/tests/test_native_text_index.py -q
```

Expected: pass.

- [ ] **Step 5: Write failing quality test for bracket OCR false positive**

Append to `backend/tests/test_text_cleaning_quality.py`:

```python
def test_diff_quality_suppresses_native_text_confirmed_bracket_false_positive(tmp_path: Path) -> None:
    pdf_path = tmp_path / "original.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "国家能源局综合司关于印发〔2006〕34号文件")
    doc.save(pdf_path)
    doc.close()

    original_document = Document(
        filename="original.pdf",
        path=str(pdf_path),
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[
            TextBlock(block_id="o1", page_no=1, text="国家能源局综合司关于印发(2006)34文件", bbox=BBox(x0=50, y0=70, x1=500, y1=95)),
        ])],
    )
    compare_document = Document(
        filename="compare.pdf",
        path="compare.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[
            TextBlock(block_id="c1", page_no=1, text="国家能源局综合司关于印发〔2006〕34号文件", bbox=BBox(x0=50, y0=70, x1=500, y1=95)),
        ])],
    )
    diff = DiffItem(
        diff_id="D075",
        diff_type="MODIFY",
        source_type="clause",
        original_text="国家能源局综合司关于印发(2006)34文件",
        compare_text="国家能源局综合司关于印发〔2006〕34号文件",
        original_snippet="(2006)34",
        compare_snippet="〔2006〕34号",
        original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=200, y0=70, x1=270, y1=95), text="(2006)34")],
        compare_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=200, y0=70, x1=280, y1=95), text="〔2006〕34号")],
        review_flags=["OCR_LOW_CONFIDENCE"],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document, compare_document=compare_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_native_text_equality"
        and decision.detail["reason"] == "native_text_confirms_changed_fragments_equal"
        for decision in result.decisions
    )
```

Also add imports near the top of the file:

```python
from pathlib import Path
import fitz
```

- [ ] **Step 6: Run the new quality test and verify it fails**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_native_text_confirmed_bracket_false_positive -q
```

Expected: fail because `DiffQualityProcessor` has no native-text equality guard.

- [ ] **Step 7: Wire native equality suppression into quality processing**

Modify `backend/app/services/diff_quality.py`:

```python
from app.services.native_text_index import NativeTextPageIndex, normalize_native_text_key
```

Inside `DiffQualityProcessor.process()`, after `_suppress_low_value_noise(...)` and before `_suppress_reciprocal_page_segmentation_drift(...)`, add:

```python
        working = self._suppress_native_text_confirmed_equal(
            working,
            decisions,
            original_document=original_document,
            compare_document=compare_document,
        )
```

Add this method to `DiffQualityProcessor`:

```python
    def _suppress_native_text_confirmed_equal(
        self,
        diffs: list[DiffItem],
        decisions: list[DiffQualityDecision],
        *,
        original_document: Document | None,
        compare_document: Document | None,
    ) -> list[DiffItem]:
        original_index = NativeTextPageIndex.from_document(original_document)
        compare_index = NativeTextPageIndex.from_document(compare_document)
        kept: list[DiffItem] = []
        for diff in diffs:
            if not self._native_text_confirms_changed_fragments_equal(diff, original_index, compare_index):
                kept.append(diff)
                continue
            decisions.append(
                DiffQualityDecision(
                    action="suppressed_by_native_text_equality",
                    diff_id=diff.diff_id,
                    detail={"reason": "native_text_confirms_changed_fragments_equal"},
                )
            )
        return kept

    def _native_text_confirms_changed_fragments_equal(
        self,
        diff: DiffItem,
        original_index: NativeTextPageIndex,
        compare_index: NativeTextPageIndex,
    ) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        original_changed = diff.original_snippet or diff.original_text
        compare_changed = diff.compare_snippet or diff.compare_text
        if not original_changed or not compare_changed:
            return False
        if normalize_native_text_key(original_changed) != normalize_native_text_key(compare_changed):
            return False
        if self._changed_text_has_business_token(diff) and not self._only_bracket_or_format_difference(original_changed, compare_changed):
            return False
        original_pages = {evidence.page_no for evidence in diff.original_evidence} or {1}
        compare_pages = {evidence.page_no for evidence in diff.compare_evidence} or original_pages
        original_confirmed = original_index.window_contains(compare_changed, original_pages) or original_index.window_contains(original_changed, original_pages)
        compare_confirmed = compare_index.window_contains(original_changed, compare_pages) or compare_index.window_contains(compare_changed, compare_pages)
        return original_confirmed or compare_confirmed

    @staticmethod
    def _only_bracket_or_format_difference(original: str, compare: str) -> bool:
        original_key = normalize_native_text_key(original)
        compare_key = normalize_native_text_key(compare)
        return bool(original_key and original_key == compare_key)
```

- [ ] **Step 8: Run quality tests for native equality**

Run:

```bash
uv run pytest backend/tests/test_native_text_index.py backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_native_text_confirmed_bracket_false_positive -q
```

Expected: pass.

- [ ] **Step 9: Commit Task 1**

Run:

```bash
git add backend/app/services/native_text_index.py backend/app/services/diff_quality.py backend/tests/test_native_text_index.py backend/tests/test_text_cleaning_quality.py
git commit -m "fix: suppress native text confirmed OCR diffs"
```

### Task 2: Non-Body Visual Noise And Signing Region Classification

**Files:**
- Create: `backend/app/services/signing_region.py`
- Modify: `backend/app/services/document_preparation.py`
- Modify: `backend/app/services/header_footer_compare.py`
- Modify: `backend/app/services/cover/patterns.py`
- Test: `backend/tests/test_text_cleaning_quality.py`
- Test: `backend/tests/test_header_footer_compare.py`
- Test: `backend/tests/test_cover_metadata.py`

- [ ] **Step 1: Write signing-region and keyword-pollution tests**

Append to `backend/tests/test_text_cleaning_quality.py`:

```python
def test_document_preparation_marks_bottom_signing_region_but_keeps_body_signing_keywords() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[
            TextBlock(
                block_id="body",
                page_no=1,
                text="13.1本合同经甲方乙方盖章后生效，签署日期以合同首页为准。",
                bbox=BBox(x0=50, y0=80, x1=520, y1=110),
            ),
            TextBlock(
                block_id="sig-label",
                page_no=1,
                text="甲方（盖章）：        乙方（盖章）：        日期：",
                bbox=BBox(x0=60, y0=690, x1=525, y1=720),
            ),
            TextBlock(
                block_id="sig-hand",
                page_no=1,
                text="黄科",
                bbox=BBox(x0=390, y0=720, x1=455, y1=760),
                confidence=0.82,
            ),
        ])],
    )

    result = DocumentPreparer().prepare(document, "compare")
    clauses = ClauseSplitter().split(document, "N")

    assert "甲方乙方盖章后生效" in "\n".join(clause.text for clause in clauses)
    assert document.pages[0].blocks[1].block_role == "signing_region"
    assert document.pages[0].blocks[1].enter_clause_compare is False
    assert document.pages[0].blocks[2].block_role == "signature_text"
    assert any(decision.reason == "signing_region_candidate" for decision in result.decisions)
```

- [ ] **Step 2: Write header/footer low-information footer noise test**

Append to `backend/tests/test_header_footer_compare.py`:

```python
def test_header_footer_ignores_low_information_repeated_footer_handwriting_fragments() -> None:
    original = _document([[], []])
    compare = _document([
        [_block("c1", "怀意", x0=410, y0=766, x1=460, y1=806, page_no=1, block_type="footer")],
        [_block("c2", "怀意", x0=412, y0=768, x1=462, y1=808, page_no=2, block_type="footer")],
    ])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []
```

- [ ] **Step 3: Write cover scanner edge noise test**

Append to `backend/tests/test_cover_metadata.py`:

```python
def test_cover_extra_ignores_scanner_edge_watermark_fragments() -> None:
    original = _document([
        _block("o_title", "技术服务合同", 190, 90, 410, 120, "doc_title"),
        _block("o_project", "项目名称：新能源场站功率预测系统授权服务", 120, 240, 500, 260),
    ])
    compare = _document([
        _block("c_edge", "有定王合全", 570, 220, 594, 520, "ocr_line", source="ppocrv5_unmatched", layout_match_status="noise_unmatched"),
        _block("c_title", "技术服务合同", 190, 90, 410, 120, "doc_title"),
        _block("c_project", "项目名称：新能源场站功率预测系统授权服务", 120, 240, 500, 260),
    ])

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    assert all("有定王合全" not in diff.compare_text for diff in diffs)
```

- [ ] **Step 4: Run the new tests and verify they fail**

Run:

```bash
uv run pytest \
  backend/tests/test_text_cleaning_quality.py::test_document_preparation_marks_bottom_signing_region_but_keeps_body_signing_keywords \
  backend/tests/test_header_footer_compare.py::test_header_footer_ignores_low_information_repeated_footer_handwriting_fragments \
  backend/tests/test_cover_metadata.py::test_cover_extra_ignores_scanner_edge_watermark_fragments \
  -q
```

Expected: fail because signing regions and scanner edge noise are not classified with these rules yet.

- [ ] **Step 5: Implement signing-region detector**

Create `backend/app/services/signing_region.py`:

```python
from __future__ import annotations

import re
import unicodedata

from app.models import Page, TextBlock


SIGNING_ANCHOR_PATTERN = re.compile(r"(甲方|乙方|签字|签名|签章|盖章|授权代表|法定代表|负责人|日期|参与人员)")
BODY_SENTENCE_PATTERN = re.compile(r"(应当|应|须|负责|承担|协商|约定|履行|支付|提供|合同经|生效|未尽事宜)")


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def is_signing_region_label(block: TextBlock, page: Page, blocks: list[TextBlock] | None = None) -> bool:
    compact = compact_text(block.text)
    if not compact or not SIGNING_ANCHOR_PATTERN.search(compact):
        return False
    if BODY_SENTENCE_PATTERN.search(compact) and len(compact) > 24:
        return False
    if _looks_like_numbered_clause(compact):
        return False
    lower_page_band = page.height > 0 and block.bbox.y0 >= page.height * 0.55
    has_form_layout = _has_signing_form_layout(compact)
    has_visual_neighbor = _has_visual_neighbor(block, blocks or [])
    has_clustered_label = _has_clustered_signing_labels(compact)
    return lower_page_band and (has_form_layout or has_visual_neighbor or has_clustered_label)


def is_signature_text_near_region(block: TextBlock, page: Page, blocks: list[TextBlock]) -> bool:
    compact = compact_text(block.text)
    if not 1 <= len(compact) <= 8:
        return False
    if SIGNING_ANCHOR_PATTERN.search(compact):
        return False
    if page.height > 0 and block.bbox.y0 < page.height * 0.50:
        return False
    return any(is_signing_region_label(candidate, page, blocks) and _near(candidate, block) for candidate in blocks)


def is_low_information_footer_artifact(text: str) -> bool:
    compact = compact_text(text)
    if not compact or len(compact) > 4:
        return False
    if re.search(r"(合同|服务|金额|日期|电话|公司|甲方|乙方)", compact):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z]+", compact))


def _looks_like_numbered_clause(compact: str) -> bool:
    return bool(re.match(r"^(?:第[一二三四五六七八九十0-9]+[章节条]|[0-9]+(?:\.[0-9]+){0,4})", compact))


def _has_signing_form_layout(compact: str) -> bool:
    label_count = len(re.findall(r"(甲方|乙方|签字|签名|签章|盖章|日期|参与人员)", compact))
    has_blank = bool(re.search(r"[:：_＿—－-]{1,}", compact))
    return label_count >= 2 or has_blank


def _has_clustered_signing_labels(compact: str) -> bool:
    return "甲方" in compact and "乙方" in compact and bool(re.search(r"(盖章|签字|签名|日期)", compact))


def _has_visual_neighbor(block: TextBlock, blocks: list[TextBlock]) -> bool:
    for other in blocks:
        if other.block_id == block.block_id:
            continue
        family = (other.block_type or "").lower()
        if family in {"seal", "stamp", "image", "figure", "signature"} and _near(block, other):
            return True
    return False


def _near(left: TextBlock, right: TextBlock) -> bool:
    horizontal_overlap = max(0.0, min(left.bbox.x1, right.bbox.x1) - max(left.bbox.x0, right.bbox.x0))
    vertical_gap = max(0.0, max(left.bbox.y0, right.bbox.y0) - min(left.bbox.y1, right.bbox.y1))
    return horizontal_overlap >= 10.0 and vertical_gap <= 90.0
```

- [ ] **Step 6: Wire signing roles into document preparation**

Modify `backend/app/services/document_preparation.py`:

```python
from app.services.signing_region import (
    is_low_information_footer_artifact,
    is_signature_text_near_region,
    is_signing_region_label,
)
```

Inside `_classify_page_structure()`, before `_is_near_signature_noise(...)`, add:

```python
            if is_signing_region_label(block, page, page.blocks):
                self._mark_role(block, side, "signing_region", "signing_region_candidate", result)
                continue
            if is_signature_text_near_region(block, page, page.blocks):
                self._mark_role(block, side, "signature_text", "signature_text_near_signing_region", result)
                continue
```

Inside `_mark_role()`, add `"signing_region"` to the non-clause role set:

```python
            "signing_region",
```

Inside `_is_repeated_bottom_text_candidate()`, before the final return, add:

```python
        if is_low_information_footer_artifact(compact):
            return True
```

- [ ] **Step 7: Tighten header/footer low-information artifact filter**

Modify `backend/app/services/header_footer_compare.py`:

```python
from app.services.signing_region import is_low_information_footer_artifact
```

Inside `_candidate()`, after `if self._looks_like_short_edge_noise(...)`, add:

```python
        if block_type in self.footer_types and is_low_information_footer_artifact(text):
            return None
```

Also add this guard to `_non_page_number_entries()` before appending an entry:

```python
            if all(is_low_information_footer_artifact(candidate.text) for candidate in group):
                continue
```

- [ ] **Step 8: Tighten cover edge noise skipping**

Modify `backend/app/services/cover/patterns.py`:

```python
def skip_extra_line(line: str, block: TextBlock, page_width: float) -> bool:
    if is_noise_line(line) or parse_labeled_line(line) or is_title_line(line):
        return True
    compact = re.sub(r"\s+", "", line)
    if not compact:
        return True
    if is_scanner_edge_noise(compact, block, page_width):
        return True
    return is_edge_noise(compact, block, page_width)


def is_scanner_edge_noise(compact: str, block: TextBlock, page_width: float) -> bool:
    if page_width <= 0:
        return False
    source = (block.source or "").lower()
    status = (block.layout_match_status or "").lower()
    block_type = (block.block_type or "").lower()
    near_edge = block.bbox.x0 <= page_width * 0.04 or block.bbox.x1 >= page_width * 0.96
    if not near_edge:
        return False
    if status == "noise_unmatched" or source.endswith("_unmatched") or block_type in {"vertical_text", "watermark", "edge_noise"}:
        return True
    return len(compact) <= 10 and not re.search(r"(合同|项目|甲方|乙方|公司|日期|金额)", compact)
```

- [ ] **Step 9: Run Task 2 tests**

Run:

```bash
uv run pytest \
  backend/tests/test_text_cleaning_quality.py::test_document_preparation_marks_bottom_signing_region_but_keeps_body_signing_keywords \
  backend/tests/test_header_footer_compare.py::test_header_footer_ignores_low_information_repeated_footer_handwriting_fragments \
  backend/tests/test_cover_metadata.py::test_cover_extra_ignores_scanner_edge_watermark_fragments \
  -q
```

Expected: pass.

- [ ] **Step 10: Run related existing tests**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py backend/tests/test_header_footer_compare.py backend/tests/test_cover_metadata.py -q
```

Expected: pass. If existing `test_header_footer_detects_repeated_low_signature_text_near_footer_band` fails because low-information names are now hidden, update it to assert meaningful repeated footer text still emits and low-information handwritten fragments do not.

- [ ] **Step 11: Commit Task 2**

Run:

```bash
git add backend/app/services/signing_region.py backend/app/services/document_preparation.py backend/app/services/header_footer_compare.py backend/app/services/cover/patterns.py backend/tests/test_text_cleaning_quality.py backend/tests/test_header_footer_compare.py backend/tests/test_cover_metadata.py
git commit -m "fix: classify signing and scan artifacts outside body diffs"
```

### Task 3: Contract Identity And Signing-Date Field Recovery

**Files:**
- Create: `backend/app/services/key_field_compare.py`
- Modify: `backend/app/services/pipeline_stages.py`
- Test: `backend/tests/test_key_field_compare.py`
- Test: `backend/tests/test_pipeline.py`

- [ ] **Step 1: Write key-field comparator tests**

Create `backend/tests/test_key_field_compare.py`:

```python
from __future__ import annotations

from app.models import BBox, Document, Page, TextBlock
from app.services.key_field_compare import build_key_field_diffs


def _block(block_id: str, text: str, page_no: int, y0: float, y1: float, block_type: str = "text") -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=text,
        bbox=BBox(x0=60, y0=y0, x1=535, y1=y1),
        block_type=block_type,
    )


def _document(pages: list[list[TextBlock]]) -> Document:
    return Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=len(pages),
        pages=[
            Page(page_no=index, width=595, height=842, blocks=[block.model_copy(update={"page_no": index}) for block in blocks])
            for index, blocks in enumerate(pages, start=1)
        ],
    )


def test_key_field_compare_reports_page_two_contract_title_and_party_a() -> None:
    original = _document([
        [_block("o-cover", "封面", 1, 80, 110)],
        [
            _block("o-title", "国能长源随州发电有限公司随县分公司2026年新能源场站功率预测系统授权服务合同", 2, 90, 135, "doc_title"),
            _block("o-party-a", "甲方：国能长源随州发电有限公司随县分公司", 2, 190, 215),
            _block("o-party-b", "乙方：国能日新科技股份有限公司", 2, 225, 250),
        ],
    ])
    compare = _document([
        [_block("c-cover", "封面", 1, 80, 110)],
        [
            _block("c-title", "长源电力随州公司2026年新能源场站功率预测系统授权服务单一来源项目合同", 2, 90, 135, "doc_title"),
            _block("c-party-a", "甲方：国能长源随州发电有限公司", 2, 190, 215),
            _block("c-party-b", "乙方：国能日新科技股份有限公司", 2, 225, 250),
        ],
    ])

    diffs = build_key_field_diffs(original, compare)

    assert [diff.title for diff in diffs] == ["合同关键字段：合同标题", "合同关键字段：甲方"]
    assert "授权服务合同" in diffs[0].original_text
    assert "单一来源项目合同" in diffs[0].compare_text
    assert "随县分公司" in diffs[1].original_snippet
    assert "KEY_FIELD_CONTRACT_IDENTITY" in diffs[0].review_flags


def test_key_field_compare_reports_focused_signing_date_fill() -> None:
    original = _document([[
        _block("o-heading", "技术协议", 1, 80, 110, "doc_title"),
        _block("o-date", "签订日期：2026年  月  日", 1, 700, 730),
    ]])
    compare = _document([[
        _block("c-heading", "技术协议", 1, 80, 110, "doc_title"),
        _block("c-date", "签订日期：2026年5月6日", 1, 700, 730),
    ]])

    diffs = build_key_field_diffs(original, compare)

    signing = [diff for diff in diffs if diff.title == "签署日期"]
    assert len(signing) == 1
    assert signing[0].source_type == "metadata"
    assert signing[0].section_type == "signature"
    assert signing[0].original_snippet == "2026年  月  日"
    assert signing[0].compare_snippet == "2026年5月6日"
    assert "SIGNING_DATE_FIELD_CHANGE" in signing[0].review_flags
```

- [ ] **Step 2: Run key-field tests and verify they fail**

Run:

```bash
uv run pytest backend/tests/test_key_field_compare.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'app.services.key_field_compare'`.

- [ ] **Step 3: Implement key field comparator**

Create `backend/app/services/key_field_compare.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from app.models import BBox, DiffItem, Document, EvidenceBox, TextBlock, TextRange
from app.services.diff_engine import DiffEngine
from app.services.native_text_index import normalize_native_text_key
from app.utils.id_utils import generate_diff_id


@dataclass(frozen=True)
class FieldValue:
    value: str
    block: TextBlock


def build_key_field_diffs(original: Document, compare: Document, start_index: int = 1) -> list[DiffItem]:
    original_fields = _extract_key_fields(original)
    compare_fields = _extract_key_fields(compare)
    diffs: list[DiffItem] = []
    next_index = start_index
    for key, title in [
        ("contract_title", "合同关键字段：合同标题"),
        ("party_a", "合同关键字段：甲方"),
        ("signing_date", "签署日期"),
    ]:
        left = original_fields.get(key)
        right = compare_fields.get(key)
        if left is None and right is None:
            continue
        left_value = left.value if left else ""
        right_value = right.value if right else ""
        if _field_key(left_value) == _field_key(right_value):
            continue
        if key == "signing_date" and not _is_blank_to_filled_signing_date(left_value, right_value):
            continue
        diffs.append(_build_field_diff(key, title, left, right, next_index))
        next_index += 1
    return diffs


def _extract_key_fields(document: Document) -> dict[str, FieldValue]:
    fields: dict[str, FieldValue] = {}
    for page in document.pages[:3]:
        for block in sorted(page.blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            text = _clean(block.text)
            compact = _compact(text)
            if not text:
                continue
            if "contract_title" not in fields and _looks_like_contract_title(block, compact):
                fields["contract_title"] = FieldValue(text, block)
            party_a = _party_value(text, "甲方")
            if party_a and "party_a" not in fields:
                fields["party_a"] = FieldValue(party_a, block)
            signing_date = _signing_date_value(text, block, page.height)
            if signing_date and "signing_date" not in fields:
                fields["signing_date"] = FieldValue(signing_date, block)
    return fields


def _looks_like_contract_title(block: TextBlock, compact: str) -> bool:
    block_type = (block.block_type or "").lower()
    if block_type in {"header", "footer", "page_footer", "seal", "table", "image"}:
        return False
    if "合同" not in compact:
        return False
    if re.search(r"[:：]", compact):
        return False
    return len(compact) >= 12 and block.bbox.y0 <= 180


def _party_value(text: str, role: str) -> str:
    match = re.search(rf"{role}\s*[:：]\s*(?P<value>[^\n|]+)", text)
    return match.group("value").strip() if match else ""


def _signing_date_value(text: str, block: TextBlock, page_height: float) -> str:
    if page_height > 0 and block.bbox.y0 < page_height * 0.45:
        return ""
    compact = _compact(text)
    if not re.search(r"(签订日期|签署日期|日期)", compact):
        return ""
    match = re.search(r"((?:19|20)\d{2}年\s*\d{0,2}\s*月\s*\d{0,2}\s*日)", text)
    return match.group(1).strip() if match else ""


def _build_field_diff(key: str, title: str, left: FieldValue | None, right: FieldValue | None, index: int) -> DiffItem:
    left_value = left.value if left else ""
    right_value = right.value if right else ""
    original_snippet, compare_snippet, original_ranges, compare_ranges = DiffEngine()._changed_snippets(left_value, right_value)
    flags = ["KEY_FIELD_CONTRACT_IDENTITY"] if key != "signing_date" else ["SIGNING_DATE_FIELD_CHANGE", "VISUAL_SIGNING_ARTIFACT"]
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="MODIFY" if left and right else "ADD" if right else "DELETE",
        title=title,
        original_text=left_value,
        compare_text=right_value,
        original_snippet=original_snippet or left_value,
        compare_snippet=compare_snippet or right_value,
        readable_change=f"{title}：{left_value} -> {right_value}",
        source_type="metadata",
        section_type="signature" if key == "signing_date" else "contract_identity",
        review_flags=flags,
        original_evidence=_field_evidence(left, original_ranges, "DELETE" if not right else "MODIFY"),
        compare_evidence=_field_evidence(right, compare_ranges, "ADD" if not left else "MODIFY"),
        original_change_ranges=original_ranges,
        compare_change_ranges=compare_ranges,
    )


def _field_evidence(field: FieldValue | None, ranges: list[TextRange], highlight_type: str) -> list[EvidenceBox]:
    if field is None:
        return []
    return [EvidenceBox(
        page_no=field.block.page_no,
        bbox=field.block.bbox,
        method="key_field",
        text=field.value,
        highlight_type=highlight_type,
        confidence=0.9,
        evidence_quality="HIGH",
        text_confidence=field.block.confidence,
    )]


def _is_blank_to_filled_signing_date(original: str, compare: str) -> bool:
    original_compact = _compact(original)
    compare_compact = _compact(compare)
    original_blank = bool(re.fullmatch(r"(?:19|20)\d{2}年月日", original_compact))
    compare_filled = bool(re.fullmatch(r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日", compare_compact))
    return original_blank and compare_filled


def _field_key(value: str) -> str:
    return normalize_native_text_key(value)


def _clean(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", _clean(text))
```

- [ ] **Step 4: Run key-field tests**

Run:

```bash
uv run pytest backend/tests/test_key_field_compare.py -q
```

Expected: pass.

- [ ] **Step 5: Write pipeline wiring test**

Append to `backend/tests/test_pipeline.py` near `PreClauseDiffStage` tests:

```python
def test_pre_clause_stage_adds_key_field_diffs_before_clause_matching(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    ctx = make_ctx(tmp_path)
    original = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=2,
        pages=[
            Page(page_no=1, width=595, height=842),
            Page(page_no=2, width=595, height=842, blocks=[
                TextBlock(block_id="o-title", page_no=2, text="国能长源随州发电有限公司随县分公司2026年新能源场站功率预测系统授权服务合同", bbox=BBox(x0=60, y0=90, x1=535, y1=135), block_type="doc_title"),
                TextBlock(block_id="o-party-a", page_no=2, text="甲方：国能长源随州发电有限公司随县分公司", bbox=BBox(x0=60, y0=190, x1=535, y1=215)),
            ]),
        ],
    )
    compare = Document(
        filename="compare.pdf",
        path="compare.pdf",
        page_count=2,
        pages=[
            Page(page_no=1, width=595, height=842),
            Page(page_no=2, width=595, height=842, blocks=[
                TextBlock(block_id="c-title", page_no=2, text="长源电力随州公司2026年新能源场站功率预测系统授权服务单一来源项目合同", bbox=BBox(x0=60, y0=90, x1=535, y1=135), block_type="doc_title"),
                TextBlock(block_id="c-party-a", page_no=2, text="甲方：国能长源随州发电有限公司", bbox=BBox(x0=60, y0=190, x1=535, y1=215)),
            ]),
        ],
    )
    ctx.set_extractions(
        ExtractionResult(document=original, extractor_used="test"),
        ExtractionResult(document=compare, extractor_used="test"),
    )

    PreClauseDiffStage(artifact_store=artifact_store).execute(ctx)

    assert any(diff.title == "合同关键字段：合同标题" for diff in ctx.metadata_diffs)
    assert any(diff.title == "合同关键字段：甲方" for diff in ctx.metadata_diffs)
```

- [ ] **Step 6: Run pipeline wiring test and verify it fails**

Run:

```bash
uv run pytest backend/tests/test_pipeline.py::test_pre_clause_stage_adds_key_field_diffs_before_clause_matching -q
```

Expected: fail because `PreClauseDiffStage` does not call `build_key_field_diffs()`.

- [ ] **Step 7: Wire key-field diffs into PreClauseDiffStage**

Modify `backend/app/services/pipeline_stages.py`:

```python
from app.services.key_field_compare import build_key_field_diffs
```

Inside `PreClauseDiffStage.execute()`, replace the metadata/table start-index block with:

```python
        cover_metadata_diffs = self.cover_metadata.build_diffs(
            original_doc,
            compare_doc,
            start_index=len(header_footer_diffs) + 1,
        )
        key_field_diffs = build_key_field_diffs(
            original_doc,
            compare_doc,
            start_index=len(header_footer_diffs) + len(cover_metadata_diffs) + 1,
        )
        metadata_diffs = [*cover_metadata_diffs, *key_field_diffs]
        _emit_progress(ctx, 39, self.name, "cover_metadata_diff_done")
        table_diffs, table_warnings = self.table_comparator.build_diffs(
            original_doc, compare_doc,
            start_index=len(header_footer_diffs) + len(metadata_diffs) + 1,
        )
```

- [ ] **Step 8: Run key-field and pipeline tests**

Run:

```bash
uv run pytest backend/tests/test_key_field_compare.py backend/tests/test_pipeline.py::test_pre_clause_stage_adds_key_field_diffs_before_clause_matching -q
```

Expected: pass.

- [ ] **Step 9: Commit Task 3**

Run:

```bash
git add backend/app/services/key_field_compare.py backend/app/services/pipeline_stages.py backend/tests/test_key_field_compare.py backend/tests/test_pipeline.py
git commit -m "fix: recover contract identity and signing date fields"
```

### Task 4: Heading Coverage And Visual Noise Quality Guards

**Files:**
- Modify: `backend/app/services/diff_quality.py`
- Modify: `backend/app/services/diff/boundary_coverage.py`
- Test: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Write tests for heading coverage and low-information visual noise**

Append to `backend/tests/test_text_cleaning_quality.py`:

```python
def test_diff_quality_suppresses_section_heading_delete_when_heading_exists_on_compare_page() -> None:
    compare_document = Document(
        filename="compare.pdf",
        path="compare.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[
            TextBlock(block_id="c1", page_no=1, text="8. 争议解决", bbox=BBox(x0=60, y0=100, x1=160, y1=125)),
            TextBlock(block_id="c2", page_no=1, text="双方发生争议时，应通过协商解决。", bbox=BBox(x0=60, y0=130, x1=500, y1=155)),
        ])],
    )
    diff = DiffItem(
        diff_id="D113",
        diff_type="DELETE",
        source_type="clause",
        original_text="争议解决",
        original_snippet="争议解决",
        original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=60, y0=100, x1=160, y1=125), text="争议解决")],
        review_flags=["SHORT_CLAUSE_MATCH_REVIEW", "READING_ORDER_RISK"],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert result.diffs == []
    assert any(decision.detail.get("reason") in {"changed_text_covered_by_opposite_page_text", "section_heading_covered_by_opposite_page"} for decision in result.decisions)


def test_diff_quality_suppresses_low_information_metadata_footer_noise() -> None:
    diff = DiffItem(
        diff_id="D002",
        diff_type="ADD",
        source_type="metadata",
        title="封面额外文本",
        compare_text="怀意",
        compare_snippet="怀意",
        compare_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=420, y0=765, x1=465, y1=805), method="cover_extra", text="怀意")],
        review_flags=["POSSIBLE_COVER_OCR_FRAGMENT", "SEAL_OR_SIGNATURE_RISK"],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(decision.detail.get("reason") == "low_information_visual_artifact" for decision in result.decisions)
```

- [ ] **Step 2: Run the new quality tests and verify they fail**

Run:

```bash
uv run pytest \
  backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_section_heading_delete_when_heading_exists_on_compare_page \
  backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_low_information_metadata_footer_noise \
  -q
```

Expected: fail because the guard reason does not exist or coverage is too narrow.

- [ ] **Step 3: Add low-information visual artifact suppression**

Modify `backend/app/services/diff_quality.py`:

```python
from app.services.signing_region import is_low_information_footer_artifact
```

Add this branch near the top of `_suppression_reason()` after cover/header checks:

```python
        if self._looks_like_low_information_visual_artifact(diff):
            return "low_information_visual_artifact"
```

Add the reason to `directly_suppressible_review_reasons`:

```python
        "low_information_visual_artifact",
```

Add the method:

```python
    def _looks_like_low_information_visual_artifact(self, diff: DiffItem) -> bool:
        if diff.source_type not in {"metadata", "header_footer", "seal"}:
            return False
        changed = self._changed_text(diff)
        if not is_low_information_footer_artifact(changed):
            return False
        if self._changed_text_has_business_token(diff):
            return False
        evidences = [*diff.original_evidence, *diff.compare_evidence]
        if not evidences:
            return True
        return all(evidence.bbox.y0 >= 700.0 or evidence.bbox.x0 <= 32.0 or evidence.bbox.x1 >= 560.0 for evidence in evidences)
```

- [ ] **Step 4: Broaden safe heading coverage**

Modify `backend/app/services/diff/boundary_coverage.py` by adding a branch in `_suppression_reason()` before `_changed_text_covered_by_opposite_page_text(...)`:

```python
        if self._section_heading_covered_by_opposite_page(diff, context):
            return "section_heading_covered_by_opposite_page"
```

Add the method:

```python
    def _section_heading_covered_by_opposite_page(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type not in {"ADD", "DELETE"}:
            return False
        changed = diff.original_snippet or diff.compare_snippet or diff.original_text or diff.compare_text
        changed_key = normalize_for_coverage(changed)
        if not changed_key or len(changed_key) > 24:
            return False
        if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]+", changed_key):
            return False
        if re.search(r"(甲方|乙方|金额|万元|元|%|期限|日期|违约|责任|第一种|第二种)", changed):
            return False
        opposite_document = context.compare_document if diff.diff_type == "DELETE" else context.original_document
        if opposite_document is None:
            return False
        candidate_pages = self._candidate_pages_for_diff(diff, context)
        if not candidate_pages:
            return False
        search_pages = {page_no + offset for page_no in candidate_pages for offset in (-1, 0, 1)}
        for page in opposite_document.pages:
            if page.page_no not in search_pages:
                continue
            for line in _page_block_lines(page):
                line_key = normalize_for_coverage(line)
                if changed_key == line_key or re.search(rf"(?:^|\d){re.escape(changed_key)}$", line_key):
                    return True
        return False
```

- [ ] **Step 5: Run new quality tests**

Run:

```bash
uv run pytest \
  backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_section_heading_delete_when_heading_exists_on_compare_page \
  backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_low_information_metadata_footer_noise \
  -q
```

Expected: pass.

- [ ] **Step 6: Run broader quality tests**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py -q
```

Expected: pass.

- [ ] **Step 7: Commit Task 4**

Run:

```bash
git add backend/app/services/diff_quality.py backend/app/services/diff/boundary_coverage.py backend/tests/test_text_cleaning_quality.py
git commit -m "fix: suppress visual noise and covered headings"
```

### Task 5: Seal Output And Frontend Visual Grouping

**Files:**
- Modify: `backend/app/services/seal_comparator.py`
- Modify: `frontend/src/pages/ResultPage.tsx`
- Test: `backend/tests/test_seal_comparator.py`
- Test: `frontend/src/pages/ResultPage.test.tsx`

- [ ] **Step 1: Write seal description test**

Append to `backend/tests/test_seal_comparator.py`:

```python
def test_seal_add_uses_region_description_for_low_confidence_ocr_text() -> None:
    seal_bbox = BBox(x0=350, y0=600, x1=430, y1=680)
    compare = _document([_block("c1", "图", BBox(x0=360, y0=620, x1=380, y1=640), layout_bbox=seal_bbox)])

    diffs = build_seal_diffs(_document([]), compare)

    assert len(diffs) == 1
    assert diffs[0].compare_text == ""
    assert diffs[0].compare_snippet == ""
    assert diffs[0].readable_change == "新增印章区域（第1页）"
    assert "VISUAL_SIGNING_ARTIFACT" in diffs[0].review_flags
```

- [ ] **Step 2: Run the seal test and verify it fails**

Run:

```bash
uv run pytest backend/tests/test_seal_comparator.py::test_seal_add_uses_region_description_for_low_confidence_ocr_text -q
```

Expected: fail because `_build_add()` currently keeps short seal OCR text.

- [ ] **Step 3: Stabilize seal add/delete output**

Modify `backend/app/services/seal_comparator.py`:

```python
def _stable_seal_text(text: str) -> str:
    normalized = _normalize_seal_text(text)
    if not normalized:
        return ""
    if _contains_html_markup(text):
        return ""
    if len(normalized) <= 2:
        return ""
    if re.fullmatch(r"[图圈圆印红点口oO0]+", normalized):
        return ""
    return text.strip()
```

In `_build_add()`:

```python
    text = _stable_seal_text(entry.text)
```

Add `review_flags=["VISUAL_SIGNING_ARTIFACT", "SEAL_REVIEW"]` to the returned `DiffItem`.

In `_build_delete()`:

```python
    text = _stable_seal_text(entry.text)
```

Add `review_flags=["VISUAL_SIGNING_ARTIFACT", "SEAL_REVIEW"]`.

In `_build_modify()`, add `review_flags=["VISUAL_SIGNING_ARTIFACT", "SEAL_REVIEW"]` to retained region text changes.

- [ ] **Step 4: Run seal tests**

Run:

```bash
uv run pytest backend/tests/test_seal_comparator.py -q
```

Expected: pass.

- [ ] **Step 5: Add frontend grouping test**

In `frontend/src/pages/ResultPage.test.tsx`, add a test near existing audit grouping tests:

```tsx
it("groups visual signing artifacts outside main diffs", async () => {
  mockApiTask({
    diffs: [
      {
        diff_id: "D025",
        diff_type: "ADD",
        title: "印章区域（第53页）",
        source_type: "seal",
        compare_text: "",
        compare_snippet: "",
        readable_change: "新增印章区域（第53页）",
        review_flags: ["VISUAL_SIGNING_ARTIFACT", "SEAL_REVIEW"],
        compare_evidence: [{ page_no: 53, bbox: { x0: 300, y0: 600, x1: 450, y1: 750 }, method: "seal_region", text: "", highlight_type: "ADD" }],
      },
      {
        diff_id: "D071",
        diff_type: "MODIFY",
        title: "争议解决",
        source_type: "clause",
        original_text: "第一种方式",
        compare_text: "第二种方式",
        original_snippet: "第一种方式",
        compare_snippet: "第二种方式",
        readable_change: "第一种方式 -> 第二种方式",
        review_flags: ["CRITICAL_VALUE_CHANGE"],
      },
    ],
  });

  render(<ResultPage />);

  expect(await screen.findByText(/争议解决/)).toBeInTheDocument();
  expect(await screen.findByText(/印章区域/)).toBeInTheDocument();
  expect(screen.getByText(/印章区域/).closest("[data-audit-group]")).toHaveAttribute("data-audit-group", "STRUCTURAL");
});
```

- [ ] **Step 6: Run frontend test and verify the missing group hook**

Run:

```bash
npm test -- --run ResultPage.test.tsx
```

Expected: fail because the group section does not expose `data-audit-group` yet.

- [ ] **Step 7: Add visual flags and group test hook to frontend grouping**

Modify `auditGroup()` in `frontend/src/pages/ResultPage.tsx`:

```tsx
    || flags.includes("VISUAL_SIGNING_ARTIFACT")
    || flags.includes("SCAN_MARK_REVIEW")
    || flags.includes("SIGNING_DATE_FIELD_CHANGE")
```

Keep `KEY_FIELD_CONTRACT_IDENTITY` in `MAIN`.

Modify the group section rendered from `groupedAuditItems(items)`:

```tsx
            <section
              className="audit-diff-group"
              key={group.group}
              aria-label={auditGroupLabels[group.group]}
              data-audit-group={group.group}
            >
```

- [ ] **Step 8: Run frontend tests**

Run:

```bash
npm test -- --run ResultPage.test.tsx
```

Expected: pass.

- [ ] **Step 9: Commit Task 5**

Run:

```bash
git add backend/app/services/seal_comparator.py backend/tests/test_seal_comparator.py frontend/src/pages/ResultPage.tsx frontend/src/pages/ResultPage.test.tsx
git commit -m "fix: stabilize seal output and visual grouping"
```

### Task 6: Task-Level Rerun Verification Scripts

**Files:**
- Create: `backend/scripts/task_4736_compare_summary.py`
- Create: `backend/scripts/render_task_pages.py`
- Test: `backend/tests/test_task_4736_verification_scripts.py`

- [ ] **Step 1: Write summary script tests**

Create `backend/tests/test_task_4736_verification_scripts.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from scripts.task_4736_compare_summary import summarize_task


def test_summarize_task_counts_source_types_and_markers(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "task.json").write_text(json.dumps({
        "diffs": [
            {"diff_id": "D002", "source_type": "metadata", "title": "封面额外文本", "compare_text": "怀意", "review_flags": []},
            {"diff_id": "D071", "source_type": "clause", "title": "争议解决", "original_text": "第一种方式", "compare_text": "第二种方式", "review_flags": ["CRITICAL_VALUE_CHANGE"]},
            {"diff_id": "D025", "source_type": "seal", "title": "印章区域（第53页）", "compare_text": "", "review_flags": ["VISUAL_SIGNING_ARTIFACT"]},
        ]
    }, ensure_ascii=False), encoding="utf-8")

    summary = summarize_task(task_dir)

    assert summary["diff_count_by_source_type"] == {"clause": 1, "metadata": 1, "seal": 1}
    assert summary["markers"]["known_option_change_present"] is True
    assert summary["markers"]["low_information_footer_noise_present"] is True
    assert summary["visual_signing_diff_ids"] == ["D025"]
```

- [ ] **Step 2: Run script tests and verify they fail**

Run:

```bash
uv run pytest backend/tests/test_task_4736_verification_scripts.py -q
```

Expected: fail because the scripts do not exist.

- [ ] **Step 3: Implement task summary script**

Create `backend/scripts/task_4736_compare_summary.py`:

```python
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any


def summarize_task(task_dir: Path) -> dict[str, Any]:
    payload = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    diffs = payload.get("diffs") or []
    source_counts = Counter(str(diff.get("source_type") or "clause") for diff in diffs)
    main_diffs = [
        {
            "diff_id": diff.get("diff_id"),
            "source_type": diff.get("source_type"),
            "title": diff.get("title"),
            "original": diff.get("original_snippet") or diff.get("original_text") or "",
            "compare": diff.get("compare_snippet") or diff.get("compare_text") or "",
            "review_flags": diff.get("review_flags") or [],
        }
        for diff in diffs
        if _is_main_diff(diff)
    ]
    visual_ids = [
        diff.get("diff_id")
        for diff in diffs
        if "VISUAL_SIGNING_ARTIFACT" in set(diff.get("review_flags") or []) or diff.get("source_type") == "seal"
    ]
    return {
        "task_id": payload.get("task_id") or task_dir.name,
        "diff_count": len(diffs),
        "diff_count_by_source_type": dict(sorted(source_counts.items())),
        "main_diffs": main_diffs,
        "visual_signing_diff_ids": [item for item in visual_ids if item],
        "markers": {
            "known_option_change_present": _contains_text_pair(diffs, "第一种方式", "第二种方式"),
            "page2_title_change_present": _contains_text_pair(diffs, "授权服务合同", "单一来源项目合同"),
            "page2_party_a_change_present": _contains_text_pair(diffs, "随县分公司", "国能长源随州发电有限公司"),
            "page36_signing_date_present": _contains_text_pair(diffs, "2026年月日", "2026年5月6日"),
            "low_information_footer_noise_present": any(_has_any_text(diff, ["怀意", "吾怀爽", "哥意", "平怀", "综"]) for diff in diffs),
            "bracket_false_positive_present": _contains_text_pair(diffs, "(2006)34", "〔2006〕34号"),
        },
    }


def _is_main_diff(diff: dict[str, Any]) -> bool:
    source_type = diff.get("source_type") or "clause"
    flags = set(diff.get("review_flags") or [])
    if source_type in {"seal", "header_footer"}:
        return False
    if flags.intersection({"VISUAL_SIGNING_ARTIFACT", "SCAN_MARK_REVIEW", "POSSIBLE_OCR_NOISE"}):
        return False
    return source_type == "clause" or "KEY_FIELD_CONTRACT_IDENTITY" in flags


def _contains_text_pair(diffs: list[dict[str, Any]], original: str, compare: str) -> bool:
    return any(_text_key(original) in _text_key(_original_text(diff)) and _text_key(compare) in _text_key(_compare_text(diff)) for diff in diffs)


def _has_any_text(diff: dict[str, Any], values: list[str]) -> bool:
    text = _text_key(f"{_original_text(diff)}\n{_compare_text(diff)}")
    return any(_text_key(value) in text for value in values)


def _original_text(diff: dict[str, Any]) -> str:
    return str(diff.get("original_snippet") or diff.get("original_text") or "")


def _compare_text(diff: dict[str, Any]) -> str:
    return str(diff.get("compare_snippet") or diff.get("compare_text") or "")


def _text_key(text: str) -> str:
    return "".join(str(text or "").split())


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    summary = summarize_task(args.task_dir)
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement render script**

Create `backend/scripts/render_task_pages.py`:

```python
from __future__ import annotations

from pathlib import Path


def render_pages(pdf_path: Path, output_dir: Path, pages: list[int], zoom: float = 2.0) -> list[Path]:
    import fitz

    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    with fitz.open(pdf_path) as pdf:
        matrix = fitz.Matrix(zoom, zoom)
        for page_no in pages:
            page = pdf[page_no - 1]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            path = output_dir / f"page-{page_no:02d}.png"
            pix.save(path)
            rendered.append(path)
    return rendered


def make_side_by_side(left: Path, right: Path, output: Path) -> Path:
    from PIL import Image

    output.parent.mkdir(parents=True, exist_ok=True)
    left_image = Image.open(left).convert("RGB")
    right_image = Image.open(right).convert("RGB")
    height = max(left_image.height, right_image.height)
    combined = Image.new("RGB", (left_image.width + right_image.width, height), "white")
    combined.paste(left_image, (0, 0))
    combined.paste(right_image, (left_image.width, 0))
    combined.save(output)
    return output


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--compare", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pages", nargs="+", type=int, required=True)
    args = parser.parse_args()
    original_paths = render_pages(args.original, args.output_dir / "original", args.pages)
    compare_paths = render_pages(args.compare, args.output_dir / "compare", args.pages)
    for original_path, compare_path in zip(original_paths, compare_paths):
        make_side_by_side(original_path, compare_path, args.output_dir / "side_by_side" / original_path.name)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run script tests**

Run:

```bash
uv run pytest backend/tests/test_task_4736_verification_scripts.py -q
```

Expected: pass.

- [ ] **Step 6: Commit Task 6**

Run:

```bash
git add backend/scripts/task_4736_compare_summary.py backend/scripts/render_task_pages.py backend/tests/test_task_4736_verification_scripts.py
git commit -m "test: add task 4736 verification scripts"
```

### Task 7: Full Verification And Task Rerun

**Files:**
- No planned code files for this task. When a verification failure identifies a defect, fix the owning file from Tasks 1-6 and rerun that task's tests before continuing.
- Artifacts: `tmp/task-4736c006-before-summary.json`, `tmp/task-4736c006-after-summary.json`, `tmp/pdfs/task-4736c006/after/`

- [ ] **Step 1: Run backend targeted tests**

Run:

```bash
uv run pytest \
  backend/tests/test_native_text_index.py \
  backend/tests/test_key_field_compare.py \
  backend/tests/test_task_4736_verification_scripts.py \
  backend/tests/test_text_cleaning_quality.py \
  backend/tests/test_header_footer_compare.py \
  backend/tests/test_cover_metadata.py \
  backend/tests/test_seal_comparator.py \
  backend/tests/test_pipeline.py \
  -q
```

Expected: pass.

- [ ] **Step 2: Run syntax and lint checks**

Run:

```bash
uv run ruff check backend/app/services backend/scripts backend/tests
uv run python -m compileall backend/app backend/tests backend/scripts
```

Expected: both commands pass.

- [ ] **Step 3: Run frontend test if frontend files changed**

Run:

```bash
npm test -- --run ResultPage.test.tsx
```

Expected: pass.

- [ ] **Step 4: Save pre-fix summary from current task state**

Run:

```bash
uv run python backend/scripts/task_4736_compare_summary.py \
  storage/tasks/4736c006-cb0e-4ef0-8b39-3594ac73aced \
  --output tmp/task-4736c006-before-summary.json
```

Expected: file exists and records current markers, including low-information footer noise and missing recovered fields where applicable.

- [ ] **Step 5: Rerun the compare task**

Run:

```bash
uv run python -c 'import json; from pathlib import Path; from app.models import CompareOptions; from app.application.compare_tasks import CompareTaskApplication; task_dir=Path("storage/tasks/4736c006-cb0e-4ef0-8b39-3594ac73aced"); payload=json.loads((task_dir/"job.json").read_text(encoding="utf-8"))["payload"]; CompareTaskApplication()._run_compare_task(original_path=Path(payload["original_path"]), compare_path=Path(payload["compare_path"]), task_id=payload["task_id"], original_filename=payload.get("original_filename"), compare_filename=payload.get("compare_filename"), compare_options=CompareOptions.model_validate(payload.get("compare_options") or {})); print("rerun_done")'
```

Expected: command prints `rerun_done` and `storage/tasks/4736c006-cb0e-4ef0-8b39-3594ac73aced/task.json` is updated.

- [ ] **Step 6: Save post-fix summary**

Run:

```bash
uv run python backend/scripts/task_4736_compare_summary.py \
  storage/tasks/4736c006-cb0e-4ef0-8b39-3594ac73aced \
  --output tmp/task-4736c006-after-summary.json
```

Expected: file exists.

- [ ] **Step 7: Render key PDF pages after rerun**

Run:

```bash
uv run python backend/scripts/render_task_pages.py \
  --original storage/tasks/4736c006-cb0e-4ef0-8b39-3594ac73aced/uploads/original_国能长源随州发电有限公司随县分公司2026年新能源场站功率预测系统授权服务合同_定版_.pdf \
  --compare storage/tasks/4736c006-cb0e-4ef0-8b39-3594ac73aced/uploads/compare_国能长源随州扫描件.pdf \
  --output-dir tmp/pdfs/task-4736c006/after \
  --pages 2 12 27 29 34 36 44 53
```

Expected: PNG files exist under `tmp/pdfs/task-4736c006/after/original/`, `tmp/pdfs/task-4736c006/after/compare/`, and `tmp/pdfs/task-4736c006/after/side_by_side/`.

- [ ] **Step 8: Inspect verification summaries**

Run:

```bash
uv run python -c 'import json; before=json.load(open("tmp/task-4736c006-before-summary.json", encoding="utf-8")); after=json.load(open("tmp/task-4736c006-after-summary.json", encoding="utf-8")); print("before", before["markers"]); print("after", after["markers"]); assert after["markers"]["known_option_change_present"]; assert after["markers"]["page2_title_change_present"]; assert after["markers"]["page2_party_a_change_present"]; assert after["markers"]["page36_signing_date_present"]; assert not after["markers"]["low_information_footer_noise_present"]; assert not after["markers"]["bracket_false_positive_present"]'
```

Expected: assertions pass.

- [ ] **Step 9: Manually inspect rendered pages**

Open or view these files:

```text
tmp/pdfs/task-4736c006/after/side_by_side/page-02.png
tmp/pdfs/task-4736c006/after/side_by_side/page-12.png
tmp/pdfs/task-4736c006/after/side_by_side/page-27.png
tmp/pdfs/task-4736c006/after/side_by_side/page-29.png
tmp/pdfs/task-4736c006/after/side_by_side/page-34.png
tmp/pdfs/task-4736c006/after/side_by_side/page-36.png
tmp/pdfs/task-4736c006/after/side_by_side/page-44.png
tmp/pdfs/task-4736c006/after/side_by_side/page-53.png
```

Expected:
- Page 2 title and party A differences are visually real and represented in post-fix summary.
- Page 27 OCR omissions are not reported as main body differences.
- Page 34 handwriting appears in visual/signing/scan group.
- Page 36 date fill appears as a focused signing-date diff.
- Page 44 bracket text is visually/native-text equal and not reported as a main diff.
- Pages 12, 29, and 53 seal/signature changes remain visible outside main body diffs.

- [ ] **Step 10: Run full backend test suite**

Run:

```bash
uv run pytest backend/tests -q
```

Expected: pass.

- [ ] **Step 11: Final commit for verification fixes**

If Task 7 required code changes, commit them:

```bash
git add backend/app backend/tests backend/scripts frontend/src
git commit -m "fix: verify task 4736 visual diff repair"
```

If Task 7 required no code changes, do not create an empty commit.

## Plan Self-Review

Spec coverage:
- Native text equality covers the `〔2006〕34号` false positive.
- Non-body visual classification covers footer handwriting, scanner edge/watermark fragments, and keyword-only signing pollution.
- Key field recovery covers page 2 title, page 2 party A, and page 36 signing date.
- Seal output covers stable region descriptions and visual grouping.
- Verification scripts cover before/after summaries, task rerun, PDF page rendering, and manual inspection paths.

Placeholder scan:
- The plan contains no reserved placeholder markers, no deferred implementation sections, and no unspecified test commands.

Type consistency:
- `DiffItem.source_type` remains compatible with existing Literal values.
- New grouping uses `review_flags` and `section_type`; frontend changes are additive.
- New scripts read existing `task.json` and do not require schema migration.
