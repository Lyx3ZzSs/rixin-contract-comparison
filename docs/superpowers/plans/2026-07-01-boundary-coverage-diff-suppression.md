# Boundary Coverage Diff Suppression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suppress clause false positives caused by boundary drift and reading-order repair when the changed text is already covered by neighboring clauses or same-page document text.

**Architecture:** Add a focused `ClauseBoundaryCoverageFilter` under `app/services/diff/` and invoke it from `DiffQualityProcessor` with optional clause/document context. The filter is conservative: it only runs on structurally risky clause diffs or short appendix heading add/delete diffs, records debug decisions, and leaves ordinary main-body clause diffs unchanged.

**Tech Stack:** Python, Pydantic models in `app.models`, existing `DiffQualityProcessor`, FastAPI comparison pipeline, pytest.

---

## File Structure

- Create `app/services/diff/boundary_coverage.py`
  - Owns boundary coverage context, normalization, eligibility checks, and suppression/downgrade decisions.
- Modify `app/services/diff_quality.py`
  - Accept optional original/compare clauses and documents.
  - Invoke `ClauseBoundaryCoverageFilter` after structural-risk flags are applied and before text confidence propagation.
- Modify `app/services/pipeline_stages.py`
  - Pass `ctx.original_clauses`, `ctx.compare_clauses`, and extraction documents into `DiffQualityProcessor.process`.
- Modify `tests/test_text_cleaning_quality.py`
  - Add unit-style regression tests for D015/D016 patterns and safety cases.
- Modify `tests/test_pipeline.py`
  - Add a focused stage wiring test proving clause/document context reaches diff quality processing.

---

### Task 1: Add Boundary Coverage Filter for Short Appendix Headings

**Files:**
- Create: `app/services/diff/boundary_coverage.py`
- Modify: `app/services/diff_quality.py`
- Test: `tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Write failing test for appendix heading covered by same-page document text**

Add imports near the top of `tests/test_text_cleaning_quality.py`:

```python
from app.models import BBox, Clause, DiffItem, Document, EvidenceBox, Page, TextBlock
```

If these names are already imported, merge the import rather than duplicating it.

Add helpers near the existing diff quality tests:

```python
def _quality_clause(
    clause_id: str,
    text: str,
    *,
    side_prefix: str = "O",
    order_index: int = 1,
    page_no: int = 1,
    section_type: str = "main_contract",
    split_flags: list[str] | None = None,
) -> Clause:
    return Clause(
        clause_id=clause_id,
        clause_no="",
        title=text.splitlines()[0] if text else "",
        text=text,
        normalized_text=re.sub(r"\s+", "", text),
        page_numbers=[page_no],
        bboxes=[
            EvidenceBox(
                page_no=page_no,
                bbox=BBox(x0=10, y0=10, x1=120, y1=30),
                text=text[:80],
            )
        ],
        source_block_ids=[f"{side_prefix.lower()}_block_{order_index}"],
        section_type=section_type,
        section_path=[text] if section_type == "appendix" else [],
        order_index=order_index,
        split_flags=split_flags or [],
    )
```

Add a helper for page OCR context:

```python
def _quality_document(page_no: int, text: str) -> Document:
    return Document(
        filename="quality.pdf",
        path="quality.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=page_no,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id=f"p{page_no}_b1",
                        page_no=page_no,
                        text=text,
                        bbox=BBox(x0=40, y0=80, x1=540, y1=140),
                        block_type="text",
                    )
                ],
            )
        ],
    )
```

Add the test:

```python
def test_diff_quality_suppresses_appendix_heading_delete_covered_by_compare_page_text() -> None:
    original_clause = _quality_clause(
        "OC093",
        "附件一:",
        order_index=93,
        page_no=13,
        section_type="appendix",
        split_flags=["SECTION_APPENDIX"],
    )
    compare_document = _quality_document(13, "附件一：\n技术服务人员表\n姓名 单位 性别")
    diff = DiffItem(
        diff_id="D016",
        diff_type="DELETE",
        source_type="clause",
        original_clause_id="OC093",
        section_type="appendix",
        section_path=["附件一:"],
        original_text="附件一:",
        original_snippet="附件一:",
        structural_flags=["SECTION_APPENDIX"],
        review_flags=["NON_MAIN_CONTRACT_SECTION"],
        original_evidence=original_clause.bboxes,
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[],
        compare_document=compare_document,
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D016"
        and decision.detail["reason"] == "short_appendix_heading_covered"
        for decision in result.decisions
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_appendix_heading_delete_covered_by_compare_page_text -q
```

Expected: FAIL with `TypeError: DiffQualityProcessor.process() got an unexpected keyword argument 'original_clauses'` or FAIL because `D016` remains in `result.diffs`.

- [ ] **Step 3: Add boundary coverage filter module**

Create `app/services/diff/boundary_coverage.py`:

```python
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.models import Clause, DiffItem, Document


STRUCTURAL_RISK_FLAGS = {
    "READING_ORDER_RISK",
    "READING_ORDER_REPAIRED",
    "PARAGRAPH_MERGED",
    "LOW_COVERAGE_MATCH_REVIEW",
    "POSSIBLE_SPLIT_DRIFT",
}

@dataclass(frozen=True)
class BoundaryCoverageContext:
    original_clauses: list[Clause] = field(default_factory=list)
    compare_clauses: list[Clause] = field(default_factory=list)
    original_document: Document | None = None
    compare_document: Document | None = None


@dataclass(frozen=True)
class BoundaryCoverageDecision:
    action: str
    diff_id: str
    detail: dict[str, object]


class ClauseBoundaryCoverageFilter:
    def filter(
        self,
        diffs: list[DiffItem],
        context: BoundaryCoverageContext,
    ) -> tuple[list[DiffItem], list[BoundaryCoverageDecision]]:
        decisions: list[BoundaryCoverageDecision] = []
        kept: list[DiffItem] = []
        for diff in diffs:
            reason = self._suppression_reason(diff, context)
            if reason:
                decisions.append(
                    BoundaryCoverageDecision(
                        action="suppressed_by_neighbor_clause_coverage",
                        diff_id=diff.diff_id,
                        detail={"reason": reason},
                    )
                )
                continue
            kept.append(diff)
        return kept, decisions

    def _suppression_reason(self, diff: DiffItem, context: BoundaryCoverageContext) -> str:
        if self._short_appendix_heading_covered(diff, context):
            return "short_appendix_heading_covered"
        return ""

    def _short_appendix_heading_covered(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type not in {"ADD", "DELETE"}:
            return False
        if diff.section_type != "appendix":
            return False
        changed = diff.original_text or diff.original_snippet if diff.diff_type == "DELETE" else diff.compare_text or diff.compare_snippet
        heading_key = appendix_heading_key(changed)
        if not heading_key:
            return False
        document = context.compare_document if diff.diff_type == "DELETE" else context.original_document
        pages = self._evidence_pages(diff)
        return any(heading_key in appendix_heading_key(text) for text in self._page_texts(document, pages))

    @staticmethod
    def _evidence_pages(diff: DiffItem) -> set[int]:
        evidences = diff.original_evidence if diff.diff_type == "DELETE" else diff.compare_evidence
        return {evidence.page_no for evidence in evidences}

    @staticmethod
    def _page_texts(document: Document | None, pages: set[int]) -> list[str]:
        if document is None or not pages:
            return []
        allowed_pages = {page_no + offset for page_no in pages for offset in (-1, 0, 1)}
        result: list[str] = []
        for page in document.pages:
            if page.page_no not in allowed_pages:
                continue
            result.extend(block.text for block in page.blocks if block.text)
        return result


def compact_text(text: str) -> str:
    raw = unicodedata.normalize("NFKC", text or "")
    raw = raw.replace("：", ":").replace("；", ";").replace("，", ",").replace("。", ".")
    return re.sub(r"\s+", "", raw).lower()


def appendix_heading_key(text: str) -> str:
    compact = compact_text(text)
    match = re.search(r"附件([一二三四五六七八九十0-9]+)[:：、.．]?", compact)
    if not match:
        return ""
    return f"附件{match.group(1)}"
```

- [ ] **Step 4: Wire filter into `DiffQualityProcessor.process`**

Modify imports in `app/services/diff_quality.py`:

```python
from app.models import Clause, DiffItem, Document, EvidenceBox
from app.services.diff.boundary_coverage import BoundaryCoverageContext, ClauseBoundaryCoverageFilter
```

Replace the existing import of `DiffItem, EvidenceBox` with the line above.

Add an initializer inside the existing `DiffQualityProcessor` class:

```python
def __init__(self) -> None:
    self.boundary_coverage_filter = ClauseBoundaryCoverageFilter()
```

Change the process signature and body:

```python
def process(
    self,
    diffs: list[DiffItem],
    *,
    original_clauses: list[Clause] | None = None,
    compare_clauses: list[Clause] | None = None,
    original_document: Document | None = None,
    compare_document: Document | None = None,
) -> DiffQualityResult:
    working = [diff.model_copy(deep=True) for diff in diffs]
    decisions: list[DiffQualityDecision] = []
    working = self._dedupe_cross_source(working, decisions)
    self._classify(working, decisions)
    working = self._suppress_low_value_noise(working, decisions)
    self._flag_structural_risks(working, decisions)
    self._flag_boundary_drift(working, decisions)
    self._flag_cross_source_structural_misclassification(working, decisions)
    working, boundary_decisions = self.boundary_coverage_filter.filter(
        working,
        BoundaryCoverageContext(
            original_clauses=original_clauses or [],
            compare_clauses=compare_clauses or [],
            original_document=original_document,
            compare_document=compare_document,
        ),
    )
    decisions.extend(
        DiffQualityDecision(
            action=decision.action,
            diff_id=decision.diff_id,
            detail=dict(decision.detail),
        )
        for decision in boundary_decisions
    )
    self._propagate_text_confidence(working)
    return DiffQualityResult(diffs=working, decisions=decisions)
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
python -m pytest tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_appendix_heading_delete_covered_by_compare_page_text -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add app/services/diff/boundary_coverage.py app/services/diff_quality.py tests/test_text_cleaning_quality.py
git commit -m "fix: suppress covered appendix heading diffs"
```

---

### Task 2: Add Neighbor Clause Coverage for Reading-Order and Boundary Drift Diffs

**Files:**
- Modify: `app/services/diff/boundary_coverage.py`
- Test: `tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Write failing test for D015-style neighbor clause coverage**

Add this test to `tests/test_text_cleaning_quality.py`:

```python
def test_diff_quality_suppresses_reading_order_contact_fields_covered_by_neighbor_clause() -> None:
    original_previous = _quality_clause(
        "OC146",
        "15.特别约定\n地址:北京市西城区广安门内大街\n482号\n联系人:环加飞\n电话:010-83582793\n传真:010-83582600",
        order_index=146,
        page_no=25,
        split_flags=["PARAGRAPH_MERGED"],
    )
    original_current = _quality_clause(
        "OC147",
        "27号金隅智造工场N6\n联系人:刘玉良\n电话:18811089109\n传真:010-83458100\nEmail: huan.jiafei@nc.sgcc.com.cn Email: yuliang.liu@sprixin.com\n统一社会信用代码:911100000536 统一社会信用代码:9111010867\n21038D",
        order_index=147,
        page_no=25,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_current = _quality_clause(
        "NC147",
        "27号金隅智造工场N6\n联系人:环加飞\n联系人:刘玉良\n电话:010-83582793\n电话:18811089109\n传真:010-83582600\n传真:010-83458100\nEmail: huan.jiafei@nc.sgcc.com.cn\nEmail: yuliang.liu@sprixin.com\n统一社会信用代码:911100000536\n统一社会信用代码:91110108672\n21038D",
        side_prefix="N",
        order_index=147,
        page_no=24,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    diff = DiffItem(
        diff_id="D015",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC147",
        compare_clause_id="NC147",
        original_text=original_current.text,
        compare_text=compare_current.text,
        original_snippet="Email: yuliang.liu@sprixin.com统一社会信用代码:9111010867",
        compare_snippet="联系人:环加飞电话:010-83582793传真:010-83582600Email: yuliang.liu@sprixin.com统一社会信用代码:91110108672",
        structural_flags=["PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_REPAIRED", "READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_previous, original_current],
        compare_clauses=[compare_current],
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D015"
        and decision.detail["reason"] == "changed_fragments_covered_by_neighbor_clauses"
        for decision in result.decisions
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_reading_order_contact_fields_covered_by_neighbor_clause -q
```

Expected: FAIL because `D015` remains in `result.diffs`.

- [ ] **Step 3: Implement clause indexes and window coverage**

Extend `app/services/diff/boundary_coverage.py` with indexed context:

```python
@dataclass
class _ClauseIndex:
    by_id: dict[str, Clause]
    ordered: list[Clause]
    positions: dict[str, int]

    @classmethod
    def from_clauses(cls, clauses: list[Clause]) -> "_ClauseIndex":
        ordered = sorted(clauses, key=lambda clause: clause.order_index)
        return cls(
            by_id={clause.clause_id: clause for clause in clauses},
            ordered=ordered,
            positions={clause.clause_id: index for index, clause in enumerate(ordered)},
        )

    def window_text(self, clause_id: str | None, radius: int = 2) -> str:
        if not clause_id or clause_id not in self.positions:
            return ""
        index = self.positions[clause_id]
        start = max(0, index - radius)
        end = min(len(self.ordered), index + radius + 1)
        return "\n".join(clause.text for clause in self.ordered[start:end])
```

Update `BoundaryCoverageContext`:

```python
@dataclass(frozen=True)
class BoundaryCoverageContext:
    original_clauses: list[Clause] = field(default_factory=list)
    compare_clauses: list[Clause] = field(default_factory=list)
    original_document: Document | None = None
    compare_document: Document | None = None

    @property
    def original_index(self) -> _ClauseIndex:
        return _ClauseIndex.from_clauses(self.original_clauses)

    @property
    def compare_index(self) -> _ClauseIndex:
        return _ClauseIndex.from_clauses(self.compare_clauses)
```

Add methods to `ClauseBoundaryCoverageFilter`:

```python
def _suppression_reason(self, diff: DiffItem, context: BoundaryCoverageContext) -> str:
    if self._short_appendix_heading_covered(diff, context):
        return "short_appendix_heading_covered"
    if self._changed_fragments_covered_by_neighbor_clauses(diff, context):
        return "changed_fragments_covered_by_neighbor_clauses"
    return ""

def _changed_fragments_covered_by_neighbor_clauses(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
    if not self._eligible_structural_clause_diff(diff):
        return False
    fragments = protected_fragments(diff.original_snippet, diff.compare_snippet)
    if not fragments:
        return False
    original_window = context.original_index.window_text(diff.original_clause_id)
    compare_window = context.compare_index.window_text(diff.compare_clause_id)
    if not original_window or not compare_window:
        return False
    original_norm = normalize_for_coverage(original_window)
    compare_norm = normalize_for_coverage(compare_window)
    return all(
        fragment.covered_by(original_norm) and fragment.covered_by(compare_norm)
        for fragment in fragments
    )

@staticmethod
def _eligible_structural_clause_diff(diff: DiffItem) -> bool:
    if diff.source_type != "clause":
        return False
    flags = set(diff.structural_flags) | set(diff.review_flags)
    return bool(flags.intersection(STRUCTURAL_RISK_FLAGS))
```

Add fragment support below the existing functions:

```python
@dataclass(frozen=True)
class CoverageFragment:
    raw: str
    normalized_values: tuple[str, ...]

    def covered_by(self, normalized_text: str) -> bool:
        return any(value and value in normalized_text for value in self.normalized_values)


def protected_fragments(original_snippet: str, compare_snippet: str) -> list[CoverageFragment]:
    text = f"{original_snippet}\n{compare_snippet}"
    fragments: list[CoverageFragment] = []
    fragments.extend(CoverageFragment(raw=value, normalized_values=(normalize_for_coverage(value),)) for value in contact_field_values(text))
    fragments.extend(CoverageFragment(raw=value, normalized_values=(normalize_email(value),)) for value in emails(text))
    fragments.extend(CoverageFragment(raw=value, normalized_values=(normalize_phone(value),)) for value in phones(text))
    fragments.extend(CoverageFragment(raw=value, normalized_values=(normalize_credit_code(value),)) for value in credit_code_candidates(text))
    unique: dict[tuple[str, ...], CoverageFragment] = {}
    for fragment in fragments:
        key = tuple(value for value in fragment.normalized_values if value)
        if key:
            unique.setdefault(key, fragment)
    return list(unique.values())


def normalize_for_coverage(text: str) -> str:
    compact = compact_text(text)
    return re.sub(r"[^0-9a-z@\u4e00-\u9fff]+", "", compact)


def contact_field_values(text: str) -> list[str]:
    pattern = re.compile(r"(?:联系人|电话|传真)[:：]\s*([A-Za-z0-9\u4e00-\u9fff@._+\-/]+)")
    return [match.group(1) for match in pattern.finditer(text or "")]


def emails(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text or "")


def phones(text: str) -> list[str]:
    return re.findall(r"(?<!\d)(?:\+?\d[\d\- ]{6,}\d)(?!\d)", text or "")


def credit_code_candidates(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    values = re.findall(r"统一社会信用代码[:：]?\s*([0-9A-Z\s]{10,24})", normalized, flags=re.IGNORECASE)
    return values


def normalize_email(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).lower()


def normalize_phone(text: str) -> str:
    return re.sub(r"\D+", "", unicodedata.normalize("NFKC", text or ""))


def normalize_credit_code(text: str) -> str:
    return re.sub(r"[^0-9A-Z]+", "", unicodedata.normalize("NFKC", text or "").upper())
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_reading_order_contact_fields_covered_by_neighbor_clause -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add app/services/diff/boundary_coverage.py tests/test_text_cleaning_quality.py
git commit -m "fix: suppress covered boundary drift clause diffs"
```

---

### Task 3: Preserve True Protected Value Changes

**Files:**
- Modify: `app/services/diff/boundary_coverage.py`
- Test: `tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Write failing test for true phone number change**

Add this test:

```python
def test_diff_quality_keeps_true_phone_change_even_with_boundary_risk() -> None:
    original_clause = _quality_clause(
        "OC010",
        "联系人:刘玉良\n电话:18811089109",
        order_index=10,
        page_no=2,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC010",
        "联系人:刘玉良\n电话:18811089110",
        side_prefix="N",
        order_index=10,
        page_no=2,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    diff = DiffItem(
        diff_id="D900",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC010",
        compare_clause_id="NC010",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="电话:18811089109",
        compare_snippet="电话:18811089110",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
    )

    assert [item.diff_id for item in result.diffs] == ["D900"]
    assert "CRITICAL_VALUE_CHANGE" in result.diffs[0].review_flags
```

- [ ] **Step 2: Run test to verify it passes before implementation**

Run:

```bash
python -m pytest tests/test_text_cleaning_quality.py::test_diff_quality_keeps_true_phone_change_even_with_boundary_risk -q
```

Expected: PASS. This verifies the Task 2 implementation is conservative for changed phone numbers.

- [ ] **Step 3: Write failing test for equal cross-line credit code**

Add this test:

```python
def test_diff_quality_suppresses_equal_credit_code_with_different_line_break() -> None:
    original_clause = _quality_clause(
        "OC020",
        "统一社会信用代码:9111010867\n23891430",
        order_index=20,
        page_no=3,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC020",
        "统一社会信用代码:91110108672\n3891430",
        side_prefix="N",
        order_index=20,
        page_no=3,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    diff = DiffItem(
        diff_id="D901",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC020",
        compare_clause_id="NC020",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="统一社会信用代码:9111010867",
        compare_snippet="统一社会信用代码:91110108672",
        structural_flags=["PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
    )

    assert result.diffs == []
    assert any(decision.action == "suppressed_by_neighbor_clause_coverage" for decision in result.decisions)
```

- [ ] **Step 4: Run credit-code test to verify it fails if current code misses continuation**

Run:

```bash
python -m pytest tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_equal_credit_code_with_different_line_break -q
```

Expected: FAIL if `credit_code_candidates` only captures the first line. PASS is acceptable if Task 2 already captures the full candidate through the window text.

- [ ] **Step 5: Make credit-code extraction include continuation digits**

If Step 4 fails, replace `credit_code_candidates` in `app/services/diff/boundary_coverage.py` with:

```python
def credit_code_candidates(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    candidates: list[str] = []
    label_pattern = re.compile(r"统一社会信用代码[:：]?", flags=re.IGNORECASE)
    for match in label_pattern.finditer(normalized):
        tail = normalized[match.end(): match.end() + 40]
        compact = re.sub(r"[^0-9A-Z]+", "", tail.upper())
        if len(compact) >= 10:
            candidates.append(compact[:18] if len(compact) >= 18 else compact)
    return candidates
```

- [ ] **Step 6: Run Task 3 tests**

Run:

```bash
python -m pytest tests/test_text_cleaning_quality.py::test_diff_quality_keeps_true_phone_change_even_with_boundary_risk tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_equal_credit_code_with_different_line_break -q
```

Expected: 2 passed.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add app/services/diff/boundary_coverage.py tests/test_text_cleaning_quality.py
git commit -m "fix: normalize protected boundary coverage values"
```

---

### Task 4: Wire Pipeline Context and Verify Regression Surface

**Files:**
- Modify: `app/services/pipeline_stages.py`
- Test: `tests/test_pipeline.py`
- Verify: targeted pytest, ruff, compileall

- [ ] **Step 1: Write failing pipeline wiring test**

In `tests/test_pipeline.py`, add a test near other `DiffQualityStage` tests:

```python
def test_diff_quality_stage_passes_clause_and_document_context(monkeypatch) -> None:
    from pathlib import Path

    from app.models import CompareTask, Document
    from app.services.extractors.base import ExtractionResult
    from app.services.pipeline import PipelineContext
    from app.services.pipeline_stages import DiffQualityStage

    captured = {}

    class CapturingProcessor:
        def process(self, diffs, *, original_clauses=None, compare_clauses=None, original_document=None, compare_document=None):
            captured["original_clauses"] = original_clauses
            captured["compare_clauses"] = compare_clauses
            captured["original_document"] = original_document
            captured["compare_document"] = compare_document
            return DiffQualityResult(diffs=diffs, decisions=[])

    task = CompareTask(task_id="quality-context-test", original_filename="o.pdf", compare_filename="c.pdf")
    original_document = Document(filename="o.pdf", path="o.pdf", page_count=0, pages=[])
    compare_document = Document(filename="c.pdf", path="c.pdf", page_count=0, pages=[])
    original_clause = Clause(
        clause_id="OC1",
        text="原文",
        normalized_text="原文",
    )
    compare_clause = Clause(
        clause_id="NC1",
        text="对比",
        normalized_text="对比",
    )
    ctx = PipelineContext(task=task, original_pdf=Path("o.pdf"), compare_pdf=Path("c.pdf"))
    ctx.original_extraction = ExtractionResult(document=original_document, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare_document, extractor_used="test")
    ctx.original_clauses = [original_clause]
    ctx.compare_clauses = [compare_clause]
    ctx.diffs = []

    stage = DiffQualityStage()
    stage.processor = CapturingProcessor()
    monkeypatch.setattr("app.services.pipeline_stages._write_debug_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.pipeline_stages._emit_progress", lambda *args, **kwargs: None)

    stage.execute(ctx)

    assert captured["original_clauses"] == [original_clause]
    assert captured["compare_clauses"] == [compare_clause]
    assert captured["original_document"] is original_document
    assert captured["compare_document"] is compare_document
```

Ensure `DiffQualityResult` and `Clause` are imported in the test file if not already available:

```python
from app.models import Clause
from app.services.diff_quality import DiffQualityResult
```

- [ ] **Step 2: Run pipeline wiring test to verify it fails**

Run:

```bash
python -m pytest tests/test_pipeline.py::test_diff_quality_stage_passes_clause_and_document_context -q
```

Expected: FAIL because `DiffQualityStage.execute` does not pass the new keyword arguments.

- [ ] **Step 3: Modify `DiffQualityStage.execute`**

In `app/services/pipeline_stages.py`, replace:

```python
result = self.processor.process(ctx.require_diffs())
```

with:

```python
extractions = ctx.require_extractions()
result = self.processor.process(
    ctx.require_diffs(),
    original_clauses=ctx.original_clauses,
    compare_clauses=ctx.compare_clauses,
    original_document=extractions.original.document,
    compare_document=extractions.compare.document,
)
```

- [ ] **Step 4: Run pipeline wiring test**

Run:

```bash
python -m pytest tests/test_pipeline.py::test_diff_quality_stage_passes_clause_and_document_context -q
```

Expected: PASS.

- [ ] **Step 5: Run all boundary coverage tests**

Run:

```bash
python -m pytest tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_appendix_heading_delete_covered_by_compare_page_text tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_reading_order_contact_fields_covered_by_neighbor_clause tests/test_text_cleaning_quality.py::test_diff_quality_keeps_true_phone_change_even_with_boundary_risk tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_equal_credit_code_with_different_line_break tests/test_pipeline.py::test_diff_quality_stage_passes_clause_and_document_context -q
```

Expected: 5 passed.

- [ ] **Step 6: Run related regression suites**

Run:

```bash
python -m pytest tests/test_text_cleaning_quality.py tests/test_pipeline.py tests/test_ocr_quality.py tests/test_ocr_remediation.py -q
```

Expected: all selected tests pass. If unrelated fixture deletions cause failures, record the exact missing fixture paths and rerun the focused tests from Step 5 plus `tests/test_text_cleaning_quality.py`.

- [ ] **Step 7: Run lint and syntax checks**

Run:

```bash
python -m ruff check app/services/diff/boundary_coverage.py app/services/diff_quality.py app/services/pipeline_stages.py tests/test_text_cleaning_quality.py tests/test_pipeline.py
python -m compileall app tests
```

Expected: ruff reports `All checks passed!`; compileall exits 0.

- [ ] **Step 8: Commit Task 4**

Run:

```bash
git add app/services/diff/boundary_coverage.py app/services/diff_quality.py app/services/pipeline_stages.py tests/test_text_cleaning_quality.py tests/test_pipeline.py
git commit -m "fix: pass boundary coverage context to diff quality"
```

---

## Self-Review Notes

- Spec coverage:
  - Appendix heading coverage is covered by Task 1.
  - Neighbor clause coverage for reading-order and paragraph-merged drift is covered by Task 2.
  - Protected value normalization and true-change preservation are covered by Task 3.
  - Pipeline integration and debug decisions are covered by Task 4.
- Scope remains limited to structurally risky clause diffs and short appendix heading add/delete diffs.
- No formal signature-page source type is introduced.
- No person names, addresses, phone numbers, emails, or task IDs are hardcoded in production code.
