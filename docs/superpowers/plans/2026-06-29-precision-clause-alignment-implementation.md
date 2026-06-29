# Precision Phase 1 条款级对齐基础设施 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建条款 fingerprint 与对齐诊断基础设施，使合同差异比对能解释条款匹配依据、标记低置信对齐，并通过 Phase 5 质量回归验证不退化。

**Architecture:** 新增 `app/services/clause_alignment.py` 作为独立、无外部依赖的条款对齐特征与诊断模块。`ClauseSplitter` 只负责把稳定 `clause_key` 写入 `Clause`；`ClauseMatcher` 继续保留现有匹配算法，只把 alignment diagnostics 写入 `ClausePair.score_details` 并在必要时降低 `match_confidence`；`CompareDebugWriter` 扩展现有 debug artifact，不改变 `/api/compare/*` 兼容结构。

**Tech Stack:** Python 3.12、Pydantic models、pytest、现有 `ClauseSplitter` / `ClauseMatcher` / `CompareDebugWriter` / Phase 5 `run_quality_regression.py`。

---

## 文件结构

- Create: `backend/app/services/clause_alignment.py`
  - 职责：生成 `ClauseAlignmentFingerprint`，提取金额/日期/百分比/数量/合同编号等关键 token，计算 alignment diagnostics 和 risk flags。
- Create: `backend/tests/test_clause_alignment.py`
  - 职责：覆盖 fingerprint、关键 token、诊断、风险标记。
- Modify: `backend/app/services/clause_splitter.py`
  - 职责：在 `_build_clauses()` 中写入更稳定的 `Clause.clause_key`。
- Modify: `backend/tests/test_text_cleaning_quality.py`
  - 职责：覆盖 `Clause.clause_key` 对格式噪声稳定。
- Modify: `backend/app/services/matcher.py`
  - 职责：将 alignment diagnostics 注入候选和最终 `ClausePair.score_details`，必要时降低 `match_confidence`。
- Modify: `backend/tests/test_matcher_optimization.py`
  - 职责：覆盖诊断写入、低置信对齐、风险标记不会丢失真实差异。
- Modify: `backend/app/services/compare_debug.py`
  - 职责：扩展 `match_matrix_summary.json`，统计 alignment 风险。
- Modify: `backend/tests/test_compare_integration.py`
  - 职责：覆盖 debug artifact 中的 alignment summary。
- Create: `docs/precision_clause_alignment_workflow.md`
  - 职责：中文说明本阶段对齐诊断的使用、回归验证方式和风险解释。

## Task 1: 新增 Clause Alignment Fingerprint

**Files:**
- Create: `backend/app/services/clause_alignment.py`
- Create: `backend/tests/test_clause_alignment.py`

- [ ] **Step 1: 写失败测试，覆盖 fingerprint 和关键 token**

Create `backend/tests/test_clause_alignment.py`:

```python
from app.models import Clause
from app.services.clause_alignment import ClauseAlignmentAnalyzer


def _clause(
    text: str,
    *,
    clause_no: str = "3.1",
    title: str = "付款条款",
    section_type: str = "main_contract",
    page_numbers: list[int] | None = None,
) -> Clause:
    return Clause(
        clause_id="C001",
        clause_no=clause_no,
        title=title,
        text=text,
        normalized_text=text,
        match_text=text,
        section_type=section_type,
        page_numbers=page_numbers or [1],
    )


def test_fingerprint_normalizes_format_noise_and_keeps_critical_tokens() -> None:
    analyzer = ClauseAlignmentAnalyzer()
    left = analyzer.fingerprint(
        _clause("甲方应在2026年6月30日前支付人民币 1,000.00 元，税率 6%。")
    )
    right = analyzer.fingerprint(
        _clause("甲方应在 2026 年 6 月 30 日前支付人民币1000.00元，税率6% 。")
    )

    assert left.clause_no_key == "3.1"
    assert left.title_key == right.title_key
    assert left.body_fingerprint == right.body_fingerprint
    assert left.critical_token_fingerprint == right.critical_token_fingerprint
    assert "amount:1000.00" in left.critical_tokens
    assert "date:2026-06-30" in left.critical_tokens
    assert "percent:6%" in left.critical_tokens


def test_fingerprint_extracts_contract_number_quantity_and_page_span() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    fingerprint = analyzer.fingerprint(
        _clause(
            "合同编号：HT-2026-001，乙方应交付10台设备。",
            clause_no="",
            title="交付",
            page_numbers=[2, 3],
        )
    )

    assert "contract_no:HT-2026-001" in fingerprint.critical_tokens
    assert "quantity:10台" in fingerprint.critical_tokens
    assert fingerprint.page_span == (2, 3)
    assert fingerprint.structure_key == "main_contract"
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
cd backend
python -m pytest tests/test_clause_alignment.py -v
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'app.services.clause_alignment'`。

- [ ] **Step 3: 实现 `clause_alignment.py`**

Create `backend/app/services/clause_alignment.py`:

```python
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.models import Clause
from app.services.normalizer import TextNormalizer


@dataclass(frozen=True)
class ClauseAlignmentFingerprint:
    clause_no_key: str
    title_key: str
    normalized_body_key: str
    body_fingerprint: str
    critical_tokens: tuple[str, ...]
    critical_token_fingerprint: str
    structure_key: str
    page_span: tuple[int, int] | None


class ClauseAlignmentAnalyzer:
    def __init__(self, normalizer: TextNormalizer | None = None) -> None:
        self.normalizer = normalizer or TextNormalizer()

    def fingerprint(self, clause: Clause) -> ClauseAlignmentFingerprint:
        clause_no_key = self._normalize_clause_no(clause.clause_no)
        title_key = self._normalize_key(clause.title)
        normalized_body_key = self._normalize_body(clause.match_text or clause.normalized_text or clause.text)
        critical_tokens = self._critical_tokens(clause.text)
        return ClauseAlignmentFingerprint(
            clause_no_key=clause_no_key,
            title_key=title_key,
            normalized_body_key=normalized_body_key,
            body_fingerprint=self._body_fingerprint(normalized_body_key),
            critical_tokens=critical_tokens,
            critical_token_fingerprint="|".join(critical_tokens),
            structure_key=self._normalize_key(clause.section_type),
            page_span=self._page_span(clause.page_numbers),
        )

    def _normalize_clause_no(self, value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "").strip()
        value = value.strip("第章节条、.． ")
        return value

    def _normalize_key(self, value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "")
        value = re.sub(r"[\s:：,，。；;、.．()（）【】\[\]《》<>]+", "", value)
        return value.lower()

    def _normalize_body(self, value: str) -> str:
        value = self.normalizer.normalize_for_diff(value or "")
        value = unicodedata.normalize("NFKC", value)
        value = re.sub(r"\s+", "", value)
        return value

    def _body_fingerprint(self, value: str) -> str:
        return value[:160]

    def _critical_tokens(self, text: str) -> tuple[str, ...]:
        text = unicodedata.normalize("NFKC", text or "")
        tokens: set[str] = set()
        tokens.update(self._date_tokens(text))
        tokens.update(self._amount_tokens(text))
        tokens.update(self._percent_tokens(text))
        tokens.update(self._contract_no_tokens(text))
        tokens.update(self._quantity_tokens(text))
        return tuple(sorted(tokens))

    def _date_tokens(self, text: str) -> set[str]:
        tokens = set()
        pattern = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
        for year, month, day in pattern.findall(text):
            tokens.add(f"date:{int(year):04d}-{int(month):02d}-{int(day):02d}")
        slash_pattern = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")
        for year, month, day in slash_pattern.findall(text):
            tokens.add(f"date:{int(year):04d}-{int(month):02d}-{int(day):02d}")
        return tokens

    def _amount_tokens(self, text: str) -> set[str]:
        tokens = set()
        pattern = re.compile(r"(?:人民币|金额|价款|费用)?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:元|万元|人民币)")
        for value in pattern.findall(text):
            tokens.add(f"amount:{value.replace(',', '')}")
        return tokens

    def _percent_tokens(self, text: str) -> set[str]:
        return {f"percent:{value}%" for value in re.findall(r"([0-9]+(?:\.\d+)?)\s*%", text)}

    def _contract_no_tokens(self, text: str) -> set[str]:
        tokens = set()
        pattern = re.compile(r"(?:合同编号|编号)[:：]?\s*([A-Za-z0-9][A-Za-z0-9_-]{3,})")
        for value in pattern.findall(text):
            tokens.add(f"contract_no:{value}")
        return tokens

    def _quantity_tokens(self, text: str) -> set[str]:
        tokens = set()
        pattern = re.compile(r"([0-9]+(?:\.\d+)?)\s*(台|套|个|件|项|批|份)")
        for value, unit in pattern.findall(text):
            tokens.add(f"quantity:{value}{unit}")
        return tokens

    def _page_span(self, page_numbers: list[int]) -> tuple[int, int] | None:
        if not page_numbers:
            return None
        return min(page_numbers), max(page_numbers)

    def text_similarity(self, left: str, right: str) -> float:
        if not left and not right:
            return 1.0
        if not left or not right:
            return 0.0
        return round(SequenceMatcher(None, left, right).ratio(), 4)

    def token_overlap(self, left: tuple[str, ...], right: tuple[str, ...]) -> float:
        left_set = set(left)
        right_set = set(right)
        if not left_set and not right_set:
            return 1.0
        if not left_set or not right_set:
            return 0.0
        return round(len(left_set & right_set) / len(left_set | right_set), 4)
```

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
cd backend
python -m pytest tests/test_clause_alignment.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/clause_alignment.py backend/tests/test_clause_alignment.py
git commit -m "feat: add clause alignment fingerprints"
```

## Task 2: 接入 ClauseSplitter 写入稳定 clause_key

**Files:**
- Modify: `backend/app/services/clause_splitter.py`
- Modify: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: 写失败测试，验证 clause_key 对格式噪声稳定**

Append to `backend/tests/test_text_cleaning_quality.py`:

```python
def test_clause_splitter_writes_stable_clause_alignment_key() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="body",
                        page_no=1,
                        text="3.1 付款 条款：甲方应在 2026 年 06 月 30 日前支付人民币 1,000.00 元。",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=120),
                    )
                ],
            )
        ],
    )

    clause = ClauseSplitter().split(document, "O")[0]

    assert clause.clause_key.startswith("3.1|")
    assert "amount:1000.00" in clause.clause_key
    assert "date:2026-06-30" in clause.clause_key
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
cd backend
python -m pytest tests/test_text_cleaning_quality.py::test_clause_splitter_writes_stable_clause_alignment_key -v
```

Expected: FAIL，当前 `clause_key` 不包含关键 token。

- [ ] **Step 3: 在 ClauseSplitter 中使用 ClauseAlignmentAnalyzer**

Modify `backend/app/services/clause_splitter.py` imports:

```python
from app.services.clause_alignment import ClauseAlignmentAnalyzer
```

Modify `ClauseSplitter.__init__`:

```python
self.alignment_analyzer = ClauseAlignmentAnalyzer(self.normalizer)
```

In `_build_clauses()`, change the direct `result.append(Clause(...))` block to create a local `clause` variable first:

```python
clause = Clause(
    clause_id=f"{prefix}C{index:03d}",
    clause_no=item["clause_no"],
    title=item["title"] or self._title_from_text(text),
    text=text,
    normalized_text=self.normalizer.normalize_for_diff(text),
    match_text=self.normalizer.normalize_for_match(text),
    page_numbers=sorted(set(item["page_numbers"])),
    bboxes=item["bboxes"],
    source_block_ids=list(dict.fromkeys(item["source_block_ids"])),
    char_boxes=char_boxes,
    segmentation_reason=item.get("segmentation_reason", ""),
    segmentation_confidence=item.get("segmentation_confidence", 0.8),
    section_type=item.get("section_type", "main_contract"),
    section_path=item.get("section_path", []),
    clause_key=self._clause_key(
        item.get("section_type", "main_contract"),
        item.get("section_path", []),
        item["clause_no"],
        text,
    ),
    order_index=index,
    split_flags=list(dict.fromkeys(item.get("split_flags", []))),
)
```

Then compute fingerprint and override `clause_key`:

```python
fingerprint = self.alignment_analyzer.fingerprint(clause)
clause = clause.model_copy(
    update={
        "clause_key": "|".join(
            part
            for part in [
                fingerprint.clause_no_key,
                fingerprint.title_key,
                fingerprint.critical_token_fingerprint,
                fingerprint.body_fingerprint[:80],
            ]
            if part
        )
    }
)
result.append(clause)
```

Keep existing `normalized_text` and `match_text` unchanged.

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
cd backend
python -m pytest tests/test_text_cleaning_quality.py::test_clause_splitter_writes_stable_clause_alignment_key tests/test_clause_alignment.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/clause_splitter.py backend/tests/test_text_cleaning_quality.py
git commit -m "feat: write clause alignment keys"
```

## Task 3: 增强 ClauseMatcher 对齐诊断

**Files:**
- Modify: `backend/app/services/clause_alignment.py`
- Modify: `backend/app/services/matcher.py`
- Modify: `backend/tests/test_clause_alignment.py`
- Modify: `backend/tests/test_matcher_optimization.py`

- [ ] **Step 1: 写失败测试，覆盖 diagnostics 和 matcher score_details**

Append to `backend/tests/test_clause_alignment.py`:

```python
def test_alignment_diagnostics_reports_match_signals() -> None:
    analyzer = ClauseAlignmentAnalyzer()
    left = _clause("甲方应在2026年6月30日前支付人民币1000元。")
    right = _clause("甲方应在2026年6月30日前支付人民币1000元。")

    diagnostics = analyzer.diagnostics(left, right)

    assert diagnostics["number_match"] is True
    assert diagnostics["title_match"] is True
    assert diagnostics["body_similarity"] == 1.0
    assert diagnostics["critical_token_overlap"] == 1.0
    assert diagnostics["section_type_match"] is True
    assert diagnostics["risk_flags"] == []
```

Append to `backend/tests/test_matcher_optimization.py`:

```python
from app.services.matcher import ClauseMatcher


def test_clause_matcher_includes_alignment_diagnostics() -> None:
    original = [
        Clause(
            clause_id="O001",
            clause_no="3.1",
            title="付款条款",
            text="甲方应在2026年6月30日前支付人民币1000元。",
            normalized_text="甲方应在2026年6月30日前支付人民币1000元。",
            match_text="甲方应在2026年6月30日前支付人民币1000元。",
            page_numbers=[1],
        )
    ]
    compare = [
        Clause(
            clause_id="N001",
            clause_no="3.1",
            title="付款条款",
            text="甲方应在2026年6月30日前支付人民币1000元。",
            normalized_text="甲方应在2026年6月30日前支付人民币1000元。",
            match_text="甲方应在2026年6月30日前支付人民币1000元。",
            page_numbers=[1],
        )
    ]

    pair = ClauseMatcher().match(original, compare)[0]

    alignment = pair.score_details["alignment"]
    assert alignment["number_match"] is True
    assert alignment["critical_token_overlap"] == 1.0
    assert alignment["risk_flags"] == []
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
cd backend
python -m pytest tests/test_clause_alignment.py::test_alignment_diagnostics_reports_match_signals tests/test_matcher_optimization.py::test_clause_matcher_includes_alignment_diagnostics -v
```

Expected: FAIL，缺少 `diagnostics()` 或 `score_details["alignment"]`。

- [ ] **Step 3: 实现 diagnostics 并接入 matcher**

Add to `ClauseAlignmentAnalyzer`:

First add the import needed by the diagnostics return type:

```python
from typing import Any
```

```python
    def diagnostics(self, original: Clause, compare: Clause) -> dict[str, Any]:
        left = self.fingerprint(original)
        right = self.fingerprint(compare)
        body_similarity = self.text_similarity(left.normalized_body_key, right.normalized_body_key)
        critical_overlap = self.token_overlap(left.critical_tokens, right.critical_tokens)
        number_match = bool(left.clause_no_key and left.clause_no_key == right.clause_no_key)
        title_match = bool(left.title_key and left.title_key == right.title_key)
        section_type_match = left.structure_key == right.structure_key
        page_distance = self._page_distance(left.page_span, right.page_span)
        return {
            "number_match": number_match,
            "title_match": title_match,
            "body_similarity": body_similarity,
            "critical_token_overlap": critical_overlap,
            "section_type_match": section_type_match,
            "page_distance": page_distance,
            "risk_flags": self.risk_flags(
                left,
                right,
                body_similarity=body_similarity,
                critical_token_overlap=critical_overlap,
            ),
        }

    def risk_flags(
        self,
        left: ClauseAlignmentFingerprint,
        right: ClauseAlignmentFingerprint,
        *,
        body_similarity: float,
        critical_token_overlap: float,
    ) -> list[str]:
        flags: list[str] = []
        if left.clause_no_key and right.clause_no_key and left.clause_no_key != right.clause_no_key and body_similarity >= 0.78:
            flags.append("TEXT_MATCH_NUMBER_MISMATCH")
        if left.clause_no_key and right.clause_no_key and left.clause_no_key == right.clause_no_key and body_similarity < 0.45:
            flags.append("TITLE_MATCH_TEXT_MISMATCH")
        if left.critical_tokens and right.critical_tokens and critical_token_overlap < 0.5:
            flags.append("CRITICAL_TOKEN_MISMATCH")
        if flags:
            flags.append("POSSIBLE_CLAUSE_MISALIGNMENT")
        return flags

    def _page_distance(
        self, left: tuple[int, int] | None, right: tuple[int, int] | None
    ) -> int | None:
        if left is None or right is None:
            return None
        if left[1] < right[0]:
            return right[0] - left[1]
        if right[1] < left[0]:
            return left[0] - right[1]
        return 0
```

Modify `backend/app/services/matcher.py`:

- Import `ClauseAlignmentAnalyzer`.
- Initialize `self.alignment_analyzer = ClauseAlignmentAnalyzer(self.normalizer)` in `ClauseMatcher.__init__`.
- In both candidate builders, after `details = self._score_details(...)`, add:

```python
details["alignment"] = self.alignment_analyzer.diagnostics(left, right)
```

- Ensure candidate summaries keep existing behavior and do not drop `alignment`.

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
cd backend
python -m pytest tests/test_clause_alignment.py tests/test_matcher_optimization.py::test_clause_matcher_includes_alignment_diagnostics -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/clause_alignment.py backend/app/services/matcher.py backend/tests/test_clause_alignment.py backend/tests/test_matcher_optimization.py
git commit -m "feat: add clause alignment diagnostics"
```

## Task 4: 低置信对齐风险标记

**Files:**
- Modify: `backend/app/services/matcher.py`
- Modify: `backend/tests/test_matcher_optimization.py`

- [ ] **Step 1: 写失败测试，覆盖风险标记和 LOW confidence**

Append to `backend/tests/test_matcher_optimization.py`:

```python
def test_clause_matcher_marks_low_confidence_alignment_for_token_conflict() -> None:
    original = [
        Clause(
            clause_id="O001",
            clause_no="3.1",
            title="付款条款",
            text="甲方应在2026年6月30日前支付人民币1000元。",
            normalized_text="甲方应在2026年6月30日前支付人民币1000元。",
            match_text="甲方应在2026年6月30日前支付人民币1000元。",
            page_numbers=[1],
        )
    ]
    compare = [
        Clause(
            clause_id="N001",
            clause_no="3.1",
            title="付款条款",
            text="甲方应在2026年6月30日前支付人民币5000元。",
            normalized_text="甲方应在2026年6月30日前支付人民币5000元。",
            match_text="甲方应在2026年6月30日前支付人民币5000元。",
            page_numbers=[1],
        )
    ]

    pair = ClauseMatcher().match(original, compare)[0]

    flags = pair.score_details["alignment"]["risk_flags"]
    assert "CRITICAL_TOKEN_MISMATCH" in flags
    assert pair.match_confidence == "LOW"
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py::test_clause_matcher_marks_low_confidence_alignment_for_token_conflict -v
```

Expected: FAIL，`match_confidence` 仍为 `NORMAL` 或缺少 risk flag。

- [ ] **Step 3: 让 matcher 根据 alignment risk 降低置信度**

Modify `_match_confidence()` in `backend/app/services/matcher.py`:

```python
alignment = candidate.details.get("alignment", {})
if isinstance(alignment, dict) and alignment.get("risk_flags"):
    return "LOW"
```

Place this before existing score threshold return logic. Do not change candidate acceptability.

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py::test_clause_matcher_marks_low_confidence_alignment_for_token_conflict tests/test_clause_alignment.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/matcher.py backend/tests/test_matcher_optimization.py
git commit -m "feat: flag risky clause alignments"
```

## Task 5: Debug Artifact 和 Summary

**Files:**
- Modify: `backend/app/services/compare_debug.py`
- Modify: `backend/tests/test_compare_integration.py`

- [ ] **Step 1: 写失败测试，覆盖 match_matrix_summary 的 alignment 风险统计**

Append to `backend/tests/test_compare_integration.py`:

```python
import json

from app.models import Clause, ClausePair
from app.infrastructure.artifact_store import ArtifactStore
from app.services.compare_debug import CompareDebugWriter


def test_match_matrix_summary_counts_alignment_risks(tmp_path: Path) -> None:
    writer = CompareDebugWriter(ArtifactStore(tmp_path))
    pairs = [
        ClausePair(
            original=Clause(clause_id="O001", text="付款1000元", normalized_text="付款1000元"),
            compare=Clause(clause_id="N001", text="付款5000元", normalized_text="付款5000元"),
            match_method="body",
            match_confidence="LOW",
            score_details={
                "alignment": {
                    "risk_flags": ["CRITICAL_TOKEN_MISMATCH", "POSSIBLE_CLAUSE_MISALIGNMENT"]
                }
            },
        )
    ]

    path = Path(writer.write_match_matrix_summary("task-1", pairs))
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["low_confidence_alignment_count"] == 1
    assert payload["alignment_risk_flag_counts"]["CRITICAL_TOKEN_MISMATCH"] == 1
```

If imports already exist in `test_compare_integration.py`, reuse them instead of duplicating.

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
cd backend
python -m pytest tests/test_compare_integration.py::test_match_matrix_summary_counts_alignment_risks -v
```

Expected: FAIL，summary 缺少 alignment risk 字段。

- [ ] **Step 3: 扩展 `write_match_matrix_summary()`**

Modify `backend/app/services/compare_debug.py` inside `write_match_matrix_summary()`:

```python
alignment_risk_counts: Counter[str] = Counter()
low_confidence_alignment_count = 0
...
alignment = pair.score_details.get("alignment", {}) if isinstance(pair.score_details, dict) else {}
risk_flags = alignment.get("risk_flags", []) if isinstance(alignment, dict) else []
if risk_flags:
    low_confidence_alignment_count += 1
    alignment_risk_counts.update(str(flag) for flag in risk_flags)
```

Add to payload:

```python
"low_confidence_alignment_count": low_confidence_alignment_count,
"alignment_risk_flag_counts": dict(alignment_risk_counts),
```

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
cd backend
python -m pytest tests/test_compare_integration.py::test_match_matrix_summary_counts_alignment_risks -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/compare_debug.py backend/tests/test_compare_integration.py
git commit -m "feat: summarize clause alignment risks"
```

## Task 6: 中文说明和回归验证

**Files:**
- Create: `docs/precision_clause_alignment_workflow.md`

- [ ] **Step 1: 写中文说明**

Create `docs/precision_clause_alignment_workflow.md`:

```markdown
# Precision Phase 1 条款级对齐说明

## 目的

本阶段用于提升合同差异比对中的条款级对齐稳定性。它不引入 LLM，也不重写 diff 引擎，而是为每个条款匹配结果提供 fingerprint、对齐诊断和低置信风险标记。

## 关键输出

条款匹配结果会在 `score_details.alignment` 中包含：

- `number_match`
- `title_match`
- `body_similarity`
- `critical_token_overlap`
- `section_type_match`
- `page_distance`
- `risk_flags`

当出现金额、日期、编号或标题等冲突时，`risk_flags` 会记录潜在风险，例如：

- `CRITICAL_TOKEN_MISMATCH`
- `TEXT_MATCH_NUMBER_MISMATCH`
- `TITLE_MATCH_TEXT_MISMATCH`
- `POSSIBLE_CLAUSE_MISALIGNMENT`

## Debug Artifact

`clause_matches.json` 可以查看每个条款对的对齐依据。

`match_matrix_summary.json` 可以查看：

- `low_confidence_alignment_count`
- `alignment_risk_flag_counts`

这些字段用于定位可能导致误报或漏报的条款错配。

## 回归验证

每次调整条款对齐逻辑后运行：

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p1 \
  --fail-on-regression
```

如果已有 baseline：

```bash
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --output-dir .ocr-compare-quality/runs/precision-p1 \
  --fail-on-regression
```

重点确认：

- `precision` 不下降。
- `recall` 不下降。
- `false_positive_count` 不增加。
- `false_negative_count` 不增加。
- `evidence_hit_rate` 不下降。

## 使用原则

本阶段遵循“先诊断，后干预”。低置信对齐先进入 debug 和质量分析，不直接等同于法律风险结论。
```

- [ ] **Step 2: 运行回归 smoke**

Run:

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p1 \
  --run-id precision-p1 \
  --fail-on-regression
```

Expected: exit 0, summary status `PASSED`, `failed_gates` is `[]`.

- [ ] **Step 3: 提交**

```bash
git add docs/precision_clause_alignment_workflow.md
git commit -m "docs: add clause alignment workflow"
```

## Task 7: 全量验证与收尾

**Files:**
- No source changes unless verification exposes issues.

- [ ] **Step 1: 运行新增与相关测试**

Run:

```bash
cd backend
python -m pytest \
  tests/test_clause_alignment.py \
  tests/test_matcher_optimization.py \
  tests/test_text_cleaning_quality.py \
  tests/test_compare_integration.py \
  -v
```

Expected: PASS。

- [ ] **Step 2: 运行语法检查**

Run:

```bash
cd backend
python -m compileall app tests scripts
```

Expected: PASS，无 Python syntax error。

- [ ] **Step 3: 运行 lint**

Run:

```bash
cd backend
python -m ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 4: 运行 backend 全量测试**

Run:

```bash
cd backend
python -m pytest
```

Expected: PASS。

- [ ] **Step 5: 运行 Phase 5 质量回归 smoke**

Run:

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p1-final \
  --run-id precision-p1-final \
  --fail-on-regression
```

Expected: exit 0, status `PASSED`, failed gates empty。

- [ ] **Step 6: 运行 frontend 验证，确认兼容**

Run:

```bash
cd frontend
npm test
```

Expected: PASS。

Run:

```bash
cd frontend
npm run build
```

Expected: PASS。

- [ ] **Step 7: 最终审查与状态确认**

Run:

```bash
git status --short --branch
git log --oneline --decorate --max-count=16
```

Expected:

- Precision Phase 1 相关源码、测试、文档已经提交。
- `.ocr-compare-quality/` 等本地运行产物保持未跟踪，不纳入提交。
- 不处理用户已有的 `frontend/src/picture/favicon.ico` 修改，除非用户明确要求。

- [ ] **Step 8: 最终 code review**

If using subagents, dispatch a final reviewer for the whole Precision Phase 1 implementation.

Final response should include:

- 实现摘要。
- 验证命令和结果。
- 关键 debug artifact / workflow 文档路径。
- 当前分支状态和未处理本地文件说明。
