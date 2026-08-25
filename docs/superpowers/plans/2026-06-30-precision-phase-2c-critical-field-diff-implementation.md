# Precision Phase 2C Critical Field Diff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a critical-field diff guard that preserves and explains contract business field changes after clause matching.

**Architecture:** Implement a focused `app.services.diff.critical_field_guard` module that detects changed amount, date, percent, duration, quantity, and party-role tokens from modify snippets plus full clause context. Integrate it in `diff.builder.build_modify()` by adding review flags and debug hints to `DiffItem.match_score_details`, then protect those diffs in `DiffQualityProcessor` from low-value suppression.

**Tech Stack:** Python 3.12, Pydantic models, existing `DiffEngine` facade, pytest, ruff, markdown docs in Chinese.

---

## File Structure

- Create `backend/app/services/diff/critical_field_guard.py`
  - Owns field token detection, token normalization, changed-field comparison, and review flag generation.
- Create `backend/tests/test_critical_field_guard.py`
  - Focused tests for every supported field type and false-positive boundaries.
- Modify `backend/app/services/diff/builder.py`
  - Applies critical field guard only to clause `MODIFY` diffs.
  - Adds `critical_field_diff_types` and `critical_field_guard_applied` to `match_score_details`.
  - Adds `CRITICAL_FIELD_CHANGE` and field-type review flags to `review_flags`.
- Modify `backend/tests/test_diff_match_patch_engine.py`
  - End-to-end `DiffEngine().build_diffs()` coverage for builder integration and range behavior.
- Modify `backend/app/services/diff_quality.py`
  - Keeps guarded critical field diffs from being suppressed as low-value noise.
  - Marks guarded field diffs as `CRITICAL_VALUE_CHANGE`.
- Modify `backend/tests/test_text_cleaning_quality.py`
  - Coverage for `DiffQualityProcessor` protection behavior.
- Create `docs/precision_critical_field_diff_workflow.md`
  - Chinese usage workflow for Phase 2C.

## Constants

Use these exact field type and review flag names:

```python
FIELD_AMOUNT = "AMOUNT"
FIELD_DATE = "DATE"
FIELD_PERCENT_RATE = "PERCENT_RATE"
FIELD_DURATION = "DURATION"
FIELD_QUANTITY = "QUANTITY"
FIELD_PARTY_ROLE = "PARTY_ROLE"

CRITICAL_FIELD_CHANGE = "CRITICAL_FIELD_CHANGE"
```

Review flags must be:

```text
CRITICAL_FIELD_CHANGE
CRITICAL_FIELD_AMOUNT_CHANGE
CRITICAL_FIELD_DATE_CHANGE
CRITICAL_FIELD_PERCENT_RATE_CHANGE
CRITICAL_FIELD_DURATION_CHANGE
CRITICAL_FIELD_QUANTITY_CHANGE
CRITICAL_FIELD_PARTY_ROLE_CHANGE
```

## Task 1: Critical Field Guard Module

**Files:**
- Create: `backend/app/services/diff/critical_field_guard.py`
- Create: `backend/tests/test_critical_field_guard.py`

- [ ] **Step 1: Write failing unit tests**

Create `backend/tests/test_critical_field_guard.py`:

```python
from __future__ import annotations

from app.services.diff.critical_field_guard import (
    critical_field_diff_types,
    critical_field_review_flags,
)


def test_detects_amount_change_from_numeric_snippet_and_full_context() -> None:
    assert critical_field_diff_types(
        "甲方应支付人民币1000元。",
        "甲方应支付人民币5000元。",
        "1000",
        "5000",
    ) == ["AMOUNT"]


def test_detects_date_change() -> None:
    assert critical_field_diff_types(
        "甲方应在2026年6月30日前付款。",
        "甲方应在2027年7月31日前付款。",
        "2026年6月30日",
        "2027年7月31日",
    ) == ["DATE"]


def test_detects_percent_rate_change() -> None:
    assert critical_field_diff_types(
        "乙方应提供6%增值税专用发票。",
        "乙方应提供13%增值税专用发票。",
        "6%",
        "13%",
    ) == ["PERCENT_RATE"]


def test_detects_chinese_percent_rate_change() -> None:
    assert critical_field_diff_types(
        "违约金按每日千分之一计算。",
        "违约金按每日千分之三计算。",
        "千分之一",
        "千分之三",
    ) == ["PERCENT_RATE"]


def test_detects_duration_change() -> None:
    assert critical_field_diff_types(
        "甲方应在30日内完成付款。",
        "甲方应在45日内完成付款。",
        "30",
        "45",
    ) == ["DURATION"]


def test_detects_chinese_workday_duration_change() -> None:
    assert critical_field_diff_types(
        "甲方应在十个工作日内完成验收。",
        "甲方应在十五个工作日内完成验收。",
        "十个工作日",
        "十五个工作日",
    ) == ["DURATION"]


def test_detects_quantity_change() -> None:
    assert critical_field_diff_types(
        "乙方应交付3台服务器。",
        "乙方应交付5台服务器。",
        "3",
        "5",
    ) == ["QUANTITY"]


def test_detects_party_role_change() -> None:
    assert critical_field_diff_types(
        "甲方负责组织验收。",
        "乙方负责组织验收。",
        "甲方",
        "乙方",
    ) == ["PARTY_ROLE"]


def test_does_not_mark_equivalent_amount_spacing() -> None:
    assert critical_field_diff_types(
        "合同金额为1000元。",
        "合同金额为1000 元。",
        "1000元",
        "1000 元",
    ) == []


def test_does_not_mark_equivalent_date_formats() -> None:
    assert critical_field_diff_types(
        "签订日期为2026.6.30。",
        "签订日期为2026-06-30。",
        "2026.6.30",
        "2026-06-30",
    ) == []


def test_does_not_mark_layout_or_punctuation_noise() -> None:
    assert critical_field_diff_types(
        "甲方应付款。",
        "甲方应付款，",
        "。",
        "，",
    ) == []


def test_review_flags_for_field_types_are_deduplicated_and_ordered() -> None:
    assert critical_field_review_flags(["AMOUNT", "DATE", "AMOUNT"]) == [
        "CRITICAL_FIELD_CHANGE",
        "CRITICAL_FIELD_AMOUNT_CHANGE",
        "CRITICAL_FIELD_DATE_CHANGE",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_critical_field_guard.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.diff.critical_field_guard'`.

- [ ] **Step 3: Implement critical field guard module**

Create `backend/app/services/diff/critical_field_guard.py`:

```python
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

FIELD_AMOUNT = "AMOUNT"
FIELD_DATE = "DATE"
FIELD_PERCENT_RATE = "PERCENT_RATE"
FIELD_DURATION = "DURATION"
FIELD_QUANTITY = "QUANTITY"
FIELD_PARTY_ROLE = "PARTY_ROLE"

CRITICAL_FIELD_CHANGE = "CRITICAL_FIELD_CHANGE"

FIELD_ORDER = [
    FIELD_AMOUNT,
    FIELD_DATE,
    FIELD_PERCENT_RATE,
    FIELD_DURATION,
    FIELD_QUANTITY,
    FIELD_PARTY_ROLE,
]

FIELD_REVIEW_FLAGS = {
    FIELD_AMOUNT: "CRITICAL_FIELD_AMOUNT_CHANGE",
    FIELD_DATE: "CRITICAL_FIELD_DATE_CHANGE",
    FIELD_PERCENT_RATE: "CRITICAL_FIELD_PERCENT_RATE_CHANGE",
    FIELD_DURATION: "CRITICAL_FIELD_DURATION_CHANGE",
    FIELD_QUANTITY: "CRITICAL_FIELD_QUANTITY_CHANGE",
    FIELD_PARTY_ROLE: "CRITICAL_FIELD_PARTY_ROLE_CHANGE",
}

AMOUNT_PATTERN = re.compile(
    r"(?:人民币|¥|￥)?\s*\d[\d,]*(?:\.\d+)?\s*(?:万|亿)?\s*元"
    r"|\d[\d,]*(?:\.\d+)?\s*(?:万元|亿元)"
)
DATE_PATTERN = re.compile(
    r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
)
PERCENT_RATE_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*[%‰]"
    r"|千分之[一二三四五六七八九十百千万零〇两\d]+"
)
DURATION_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:个工作日|工作日|日|天|个月|月|年)"
    r"|[一二三四五六七八九十百千万零〇两]+个工作日"
)
QUANTITY_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:台|套|个|项|批|份|件|人天)"
)
PARTY_ROLE_PATTERN = re.compile(r"甲方|乙方|买方|卖方|供应商|客户")

FIELD_PATTERNS = {
    FIELD_AMOUNT: AMOUNT_PATTERN,
    FIELD_DATE: DATE_PATTERN,
    FIELD_PERCENT_RATE: PERCENT_RATE_PATTERN,
    FIELD_DURATION: DURATION_PATTERN,
    FIELD_QUANTITY: QUANTITY_PATTERN,
    FIELD_PARTY_ROLE: PARTY_ROLE_PATTERN,
}


@dataclass(frozen=True)
class FieldToken:
    field_type: str
    raw: str
    normalized: str


def critical_field_diff_types(
    original_text: str,
    compare_text: str,
    original_snippet: str,
    compare_snippet: str,
) -> list[str]:
    if not _compact(original_snippet) or not _compact(compare_snippet):
        return []
    field_types: list[str] = []
    for field_type in FIELD_ORDER:
        left_tokens = _changed_field_tokens(original_text, original_snippet, field_type)
        right_tokens = _changed_field_tokens(compare_text, compare_snippet, field_type)
        if not left_tokens and not right_tokens:
            continue
        if _token_values(left_tokens) != _token_values(right_tokens):
            field_types.append(field_type)
    return field_types


def critical_field_review_flags(field_types: list[str]) -> list[str]:
    flags: list[str] = []
    for field_type in field_types:
        flag = FIELD_REVIEW_FLAGS.get(field_type)
        if flag and flag not in flags:
            flags.append(flag)
    if not flags:
        return []
    return [CRITICAL_FIELD_CHANGE, *flags]


def _changed_field_tokens(text: str, snippet: str, field_type: str) -> list[FieldToken]:
    pattern = FIELD_PATTERNS[field_type]
    tokens: list[FieldToken] = []
    for raw in _pattern_values(pattern, text):
        if _token_touches_change(raw, snippet):
            tokens.append(FieldToken(field_type, raw, _normalize_token(field_type, raw)))
    for raw in _pattern_values(pattern, snippet):
        tokens.append(FieldToken(field_type, raw, _normalize_token(field_type, raw)))
    return _dedupe_tokens(tokens)


def _pattern_values(pattern: re.Pattern[str], text: str) -> list[str]:
    return [match.group(0) for match in pattern.finditer(text or "")]


def _token_touches_change(raw_token: str, snippet: str) -> bool:
    token = _compact(raw_token)
    changed = _compact(snippet)
    if not token or not changed:
        return False
    if token in changed or changed in token:
        return True
    token_numbers = set(re.findall(r"\d+(?:\.\d+)?", token))
    snippet_numbers = set(re.findall(r"\d+(?:\.\d+)?", changed))
    return bool(token_numbers and snippet_numbers and token_numbers.intersection(snippet_numbers))


def _token_values(tokens: list[FieldToken]) -> list[str]:
    return [token.normalized for token in tokens]


def _dedupe_tokens(tokens: list[FieldToken]) -> list[FieldToken]:
    result: list[FieldToken] = []
    seen: set[tuple[str, str]] = set()
    for token in tokens:
        key = (token.field_type, token.normalized)
        if key in seen:
            continue
        seen.add(key)
        result.append(token)
    return result


def _normalize_token(field_type: str, raw: str) -> str:
    compact = _compact(raw)
    if field_type == FIELD_DATE:
        canonical = _canonical_date(compact)
        if canonical:
            return canonical
    if field_type == FIELD_AMOUNT:
        return compact.replace(",", "")
    return compact


def _canonical_date(compact: str) -> str:
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", compact)
    if match:
        return _date_key(match)
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", compact)
    if match:
        return _date_key(match)
    return ""


def _date_key(match: re.Match[str]) -> str:
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _compact(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", "", normalized)
```

- [ ] **Step 4: Run guard tests**

Run:

```bash
cd backend
python -m pytest tests/test_critical_field_guard.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/services/diff/critical_field_guard.py backend/tests/test_critical_field_guard.py
git commit -m "feat: add critical field diff guard"
```

## Task 2: Diff Builder Integration

**Files:**
- Modify: `backend/app/services/diff/builder.py`
- Modify: `backend/tests/test_diff_match_patch_engine.py`

- [ ] **Step 1: Write failing builder integration tests**

Append these tests after `test_diff_match_patch_engine_maps_changes_back_to_text_ranges()` in `backend/tests/test_diff_match_patch_engine.py`:

```python
def test_diff_builder_marks_critical_amount_field_change() -> None:
    diff = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text="合同金额为100万元", normalized_text="合同金额为100万元"),
                compare=Clause(clause_id="N001", text="合同金额为120万元", normalized_text="合同金额为120万元"),
            )
        ]
    )[0]

    assert "CRITICAL_FIELD_CHANGE" in diff.review_flags
    assert "CRITICAL_FIELD_AMOUNT_CHANGE" in diff.review_flags
    assert diff.match_score_details["critical_field_diff_types"] == ["AMOUNT"]
    assert diff.match_score_details["critical_field_guard_applied"] == 1.0
    assert [diff.original_text[item.start:item.end] for item in diff.original_change_ranges] == ["100"]
    assert [diff.compare_text[item.start:item.end] for item in diff.compare_change_ranges] == ["120"]


def test_diff_builder_marks_critical_date_field_change() -> None:
    diff = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(
                    clause_id="O001",
                    text="甲方应在2026年6月30日前付款。",
                    normalized_text="甲方应在2026年6月30日前付款。",
                ),
                compare=Clause(
                    clause_id="N001",
                    text="甲方应在2027年7月31日前付款。",
                    normalized_text="甲方应在2027年7月31日前付款。",
                ),
            )
        ]
    )[0]

    assert "CRITICAL_FIELD_CHANGE" in diff.review_flags
    assert "CRITICAL_FIELD_DATE_CHANGE" in diff.review_flags
    assert diff.match_score_details["critical_field_diff_types"] == ["DATE"]


def test_diff_builder_does_not_mark_punctuation_only_change_as_critical_field() -> None:
    diffs = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text="甲方应付款。", normalized_text="甲方应付款。"),
                compare=Clause(clause_id="N001", text="甲方应付款，", normalized_text="甲方应付款，"),
            )
        ]
    )

    assert diffs
    assert "CRITICAL_FIELD_CHANGE" not in diffs[0].review_flags
    assert "critical_field_diff_types" not in diffs[0].match_score_details
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_diff_match_patch_engine.py::test_diff_builder_marks_critical_amount_field_change tests/test_diff_match_patch_engine.py::test_diff_builder_marks_critical_date_field_change tests/test_diff_match_patch_engine.py::test_diff_builder_does_not_mark_punctuation_only_change_as_critical_field -v
```

Expected: first two tests FAIL because `CRITICAL_FIELD_CHANGE` and `critical_field_diff_types` are not written yet.

- [ ] **Step 3: Integrate guard into diff builder**

In `backend/app/services/diff/builder.py`, add this import near the other `app.services.diff` imports:

```python
from app.services.diff.critical_field_guard import (
    critical_field_diff_types,
    critical_field_review_flags,
)
```

In `build_modify()`, replace:

```python
    flags = review_flags(pair)
    if line_pairing_reasons:
```

with:

```python
    flags = review_flags(pair)
    score_details = dict(pair.score_details)
    field_types = critical_field_diff_types(
        left.text,
        right.text,
        original_snippet,
        compare_snippet,
    )
    if field_types:
        flags.extend(critical_field_review_flags(field_types))
        score_details["critical_field_diff_types"] = field_types
        score_details["critical_field_guard_applied"] = 1.0
    if line_pairing_reasons:
```

Then in the `DiffItem` constructor inside `build_modify()`, replace:

```python
        match_score_details=pair.score_details,
```

with:

```python
        match_score_details=score_details,
```

- [ ] **Step 4: Run focused builder tests**

Run:

```bash
cd backend
python -m pytest tests/test_diff_match_patch_engine.py::test_diff_builder_marks_critical_amount_field_change tests/test_diff_match_patch_engine.py::test_diff_builder_marks_critical_date_field_change tests/test_diff_match_patch_engine.py::test_diff_builder_does_not_mark_punctuation_only_change_as_critical_field -v
```

Expected: PASS.

- [ ] **Step 5: Run related diff tests**

Run:

```bash
cd backend
python -m pytest tests/test_critical_field_guard.py tests/test_diff_match_patch_engine.py tests/test_range_refiner.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add backend/app/services/diff/builder.py backend/tests/test_diff_match_patch_engine.py
git commit -m "feat: mark critical field clause diffs"
```

## Task 3: Diff Quality Protection

**Files:**
- Modify: `backend/app/services/diff_quality.py`
- Modify: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Write failing quality protection tests**

Append these tests after `test_diff_quality_suppresses_short_symbol_noise_without_business_tokens()` in `backend/tests/test_text_cleaning_quality.py`:

```python
def test_diff_quality_preserves_critical_field_change_from_low_value_suppression() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="/",
        compare_snippet="∠",
        match_score=99,
        review_flags=["CRITICAL_FIELD_CHANGE", "CRITICAL_FIELD_AMOUNT_CHANGE"],
        match_score_details={
            "critical_field_diff_types": ["AMOUNT"],
            "critical_field_guard_applied": 1.0,
        },
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D001"]
    assert "CRITICAL_FIELD_CHANGE" in result.diffs[0].review_flags
    assert "CRITICAL_VALUE_CHANGE" in result.diffs[0].review_flags
    assert not any(decision.action == "suppressed_low_value_noise" for decision in result.decisions)


def test_diff_quality_keeps_ocr_review_status_on_critical_field_change() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="1000元",
        compare_snippet="5000元",
        review_flags=[
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_AMOUNT_CHANGE",
            "EVIDENCE_UNRELIABLE",
        ],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff]).diffs[0]

    assert result.quality_status == "NEEDS_REVIEW"
    assert "CRITICAL_VALUE_CHANGE" in result.review_flags
    assert "EVIDENCE_UNRELIABLE" in result.review_flags
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend
python -m pytest tests/test_text_cleaning_quality.py::test_diff_quality_preserves_critical_field_change_from_low_value_suppression tests/test_text_cleaning_quality.py::test_diff_quality_keeps_ocr_review_status_on_critical_field_change -v
```

Expected: first test FAILS because the low-value symbol diff is suppressed or not marked `CRITICAL_VALUE_CHANGE`.

- [ ] **Step 3: Add critical field protection helpers**

In `backend/app/services/diff_quality.py`, add this class-level constant near `ocr_quality_review_flags`:

```python
    critical_field_flag = "CRITICAL_FIELD_CHANGE"
```

In `_classify()`, after the `_should_downgrade_non_body_change(diff)` branch and before the `_looks_like_minor_ocr_noise` branch, insert:

```python
            if self._has_critical_field_change(diff):
                self._add_flag(diff, "CRITICAL_VALUE_CHANGE")
                decisions.append(DiffQualityDecision(action="critical_field_change", diff_id=diff.diff_id))
                continue
```

In `_suppression_reason()`, insert this block after the header/footer noise check:

```python
        if self._has_critical_field_change(diff):
            return ""
```

Add this method near `_is_critical_change()`:

```python
    def _has_critical_field_change(self, diff: DiffItem) -> bool:
        return self.critical_field_flag in diff.review_flags
```

In `_is_critical_change()`, insert at the top:

```python
        if self._has_critical_field_change(diff):
            return True
```

- [ ] **Step 4: Run focused quality tests**

Run:

```bash
cd backend
python -m pytest tests/test_text_cleaning_quality.py::test_diff_quality_preserves_critical_field_change_from_low_value_suppression tests/test_text_cleaning_quality.py::test_diff_quality_keeps_ocr_review_status_on_critical_field_change -v
```

Expected: PASS.

- [ ] **Step 5: Run related quality tests**

Run:

```bash
cd backend
python -m pytest tests/test_text_cleaning_quality.py tests/test_diff_match_patch_engine.py tests/test_critical_field_guard.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add backend/app/services/diff_quality.py backend/tests/test_text_cleaning_quality.py
git commit -m "feat: protect critical field diffs from suppression"
```

## Task 4: Chinese Workflow Documentation

**Files:**
- Create: `docs/precision_critical_field_diff_workflow.md`

- [ ] **Step 1: Create Chinese workflow doc**

Create `docs/precision_critical_field_diff_workflow.md` with this content:

````markdown
# Precision Phase 2C：关键字段差异保护工作流

## 目标

Phase 2C 用于保护合同关键字段变化。它不做 LLM 复核，也不重构字段抽取系统，而是在 clause `MODIFY` diff 生成后识别高价值字段变化，给 diff 写入可回归的 flags 和 debug hints。

## 新增输出

命中字段 guard 的 diff 会包含：


查看实际任务或回归 case 的 diff payload：

```text
diffs[].review_flags
diffs[].match_score_details.critical_field_diff_types
diffs[].match_score_details.critical_field_guard_applied

- [ ] **Step 2: Commit Task 4**

```bash
git add docs/precision_critical_field_diff_workflow.md
git commit -m "docs: add critical field diff workflow"
```

## Task 5: Final Verification

**Files:**
- Verify only; no new implementation expected.

- [ ] **Step 1: Run Phase 2C focused tests**

Run:

```bash
cd backend
python -m pytest tests/test_critical_field_guard.py tests/test_diff_match_patch_engine.py tests/test_text_cleaning_quality.py -v
```

Expected: PASS.

- [ ] **Step 3: Run backend syntax check**

Run:

```bash
cd backend
python -m compileall app tests
```

Expected: command exits with code 0.

- [ ] **Step 4: Run backend lint**

Run:

```bash
cd backend
python -m ruff check .
```

Expected: `All checks passed!`


- [ ] **Step 6: Run full backend tests**

Run:

```bash
cd backend
python -m pytest
```

Expected: PASS.

- [ ] **Step 7: Check git status**

Run:

```bash
git status --short
```


## Self-Review Checklist
