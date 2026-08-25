# Precision Phase 2B Risk Aware Matcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a risk-aware matcher guard layer that lowers overconfident risky clause matches and exposes the guard decisions to quality attribution.

**Architecture:** Keep the current `ClauseMatcher` scoring pipeline and add a private guard helper that writes `score_details.matcher_risk_flags` plus `score_details.matcher_guard_applied`. Then use those flags in score capping, candidate acceptance, and match confidence without changing public API models.


---

## File Structure


## Guard Flag Definitions

Use these exact string constants in `backend/app/services/matcher.py`:

```python
SAME_KEY_LOW_BODY_COVERAGE = "SAME_KEY_LOW_BODY_COVERAGE"
CRITICAL_TOKEN_CONFLICT = "CRITICAL_TOKEN_CONFLICT"
SAME_NUMBER_LOW_BODY_SIMILARITY = "SAME_NUMBER_LOW_BODY_SIMILARITY"
BODY_ONLY_ALIGNMENT_RISK = "BODY_ONLY_ALIGNMENT_RISK"
```

`matcher_guard_applied` must be `1.0` when `matcher_risk_flags` is non-empty, otherwise `0.0`.

## Task 1: Matcher Guard Metadata

**Files:**
- Modify: `backend/tests/test_matcher_optimization.py`
- Modify: `backend/app/services/matcher.py`

- [ ] **Step 1: Write failing tests for matcher risk metadata**

Append these tests after `test_clause_matcher_marks_low_confidence_alignment_for_token_conflict()` in `backend/tests/test_matcher_optimization.py`:

```python
def test_clause_matcher_writes_matcher_guard_for_low_coverage_same_key() -> None:
    original = [
        clause(
            "O001",
            "",
            "签署页",
            "甲方:国家电网有限公司华北分部\n乙方:国能日新科技股份有限公司\n地址:北京市海淀区建材城中路2482号",
            section_type="main_contract",
            clause_key="main_contract/签署页",
        )
    ]
    compare = [
        clause(
            "N001",
            "",
            "签署页",
            "地址:北京市海淀区建材城中路2482号",
            section_type="main_contract",
            clause_key="main_contract/签署页",
        )
    ]

    pair = ClauseMatcher().match(original, compare)[0]

    assert pair.score_details["matcher_risk_flags"] == ["SAME_KEY_LOW_BODY_COVERAGE"]
    assert pair.score_details["matcher_guard_applied"] == 1.0
    assert pair.match_candidates[0]["score_details"]["matcher_risk_flags"] == ["SAME_KEY_LOW_BODY_COVERAGE"]


def test_clause_matcher_writes_matcher_guard_for_critical_token_conflict() -> None:
    original = [clause("O001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币1000元。")]
    compare = [clause("N001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币5000元。")]

    pair = ClauseMatcher().match(original, compare)[0]

    assert "CRITICAL_TOKEN_CONFLICT" in pair.score_details["matcher_risk_flags"]
    assert pair.score_details["matcher_guard_applied"] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py::test_clause_matcher_writes_matcher_guard_for_low_coverage_same_key tests/test_matcher_optimization.py::test_clause_matcher_writes_matcher_guard_for_critical_token_conflict -v
```

Expected: FAIL because `matcher_risk_flags` does not exist yet.

- [ ] **Step 3: Add matcher guard helper**

In `backend/app/services/matcher.py`, add constants after `logger = logging.getLogger(__name__)`:

```python
SAME_KEY_LOW_BODY_COVERAGE = "SAME_KEY_LOW_BODY_COVERAGE"
CRITICAL_TOKEN_CONFLICT = "CRITICAL_TOKEN_CONFLICT"
SAME_NUMBER_LOW_BODY_SIMILARITY = "SAME_NUMBER_LOW_BODY_SIMILARITY"
BODY_ONLY_ALIGNMENT_RISK = "BODY_ONLY_ALIGNMENT_RISK"
```

In `_score_details()`, replace:

```python
        details["alignment"] = self.alignment_analyzer.diagnostics(left, right)
        return details
```

with:

```python
        details["alignment"] = self.alignment_analyzer.diagnostics(left, right)
        self._apply_matcher_guard_details(details)
        return details
```

Add these private methods directly below `_score_details()`:

```python
    def _apply_matcher_guard_details(self, details: dict[str, Any]) -> None:
        flags = self._matcher_risk_flags(details)
        details["matcher_risk_flags"] = flags
        details["matcher_guard_applied"] = 1.0 if flags else 0.0

    def _matcher_risk_flags(self, details: dict[str, Any]) -> list[str]:
        flags: list[str] = []
        alignment = details.get("alignment")
        alignment_flags = self._alignment_risk_flags(alignment)
        body_similarity = self._alignment_number(alignment, "body_similarity", default=1.0)

        if (
            details.get("clause_key_score", 0.0) >= 96
            and details.get("body_length_coverage", 1.0) < 0.70
        ):
            flags.append(SAME_KEY_LOW_BODY_COVERAGE)

        if "CRITICAL_TOKEN_MISMATCH" in alignment_flags:
            flags.append(CRITICAL_TOKEN_CONFLICT)

        if (
            details.get("clause_no_score", 0.0) >= 100
            and body_similarity < 0.45
        ):
            flags.append(SAME_NUMBER_LOW_BODY_SIMILARITY)

        if (
            alignment_flags
            and details.get("clause_key_score", 0.0) < 96
            and details.get("clause_no_score", 0.0) < 100
            and details.get("body_score", 0.0) >= min(self.threshold, 78)
        ):
            flags.append(BODY_ONLY_ALIGNMENT_RISK)

        return flags

    @staticmethod
    def _alignment_risk_flags(alignment: Any) -> set[str]:
        if not isinstance(alignment, dict):
            return set()
        raw_flags = alignment.get("risk_flags")
        if not isinstance(raw_flags, list | tuple | set):
            return set()
        return {flag for flag in raw_flags if isinstance(flag, str) and flag}

    @staticmethod
    def _alignment_number(alignment: Any, field: str, *, default: float) -> float:
        if not isinstance(alignment, dict):
            return default
        value = alignment.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return default
```

- [ ] **Step 4: Run the focused metadata tests**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py::test_clause_matcher_writes_matcher_guard_for_low_coverage_same_key tests/test_matcher_optimization.py::test_clause_matcher_writes_matcher_guard_for_critical_token_conflict -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/services/matcher.py backend/tests/test_matcher_optimization.py
git commit -m "feat: add matcher guard metadata"
```

## Task 2: Score Caps And Acceptance Guards

**Files:**
- Modify: `backend/tests/test_matcher_optimization.py`
- Modify: `backend/app/services/matcher.py`

- [ ] **Step 1: Write failing tests for guarded score and candidate acceptance**

Append these tests near the existing low-coverage and numeric-change tests in `backend/tests/test_matcher_optimization.py`:

```python
def test_low_coverage_same_key_with_weak_body_and_title_is_not_accepted_by_key_only() -> None:
    original = [
        clause(
            "O001",
            "",
            "服务范围",
            "乙方应提供功率预测平台部署、模型训练、接口联调、历史数据迁移、验收支持和上线后运维服务。",
            clause_key="main_contract/服务范围",
        )
    ]
    compare = [
        clause(
            "N001",
            "",
            "项目联系人",
            "联系人:张三。",
            clause_key="main_contract/服务范围",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert {pair.match_method for pair in pairs} == {"delete", "add"}
    delete_pair = next(pair for pair in pairs if pair.match_method == "delete")
    assert delete_pair.match_candidates[0]["score_details"]["matcher_risk_flags"] == [
        "SAME_KEY_LOW_BODY_COVERAGE"
    ]


def test_critical_token_conflict_caps_score_but_keeps_modify_candidate() -> None:
    original = [clause("O001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币1000元。")]
    compare = [clause("N001", "3.1", "付款条款", "甲方应在2027年7月31日前支付人民币5000元。")]

    pair = ClauseMatcher().match(original, compare)[0]

    assert pair.compare is not None
    assert pair.score <= 84.0
    assert "CRITICAL_TOKEN_CONFLICT" in pair.score_details["matcher_risk_flags"]
    assert pair.match_confidence == "LOW"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py::test_low_coverage_same_key_with_weak_body_and_title_is_not_accepted_by_key_only tests/test_matcher_optimization.py::test_critical_token_conflict_caps_score_but_keeps_modify_candidate -v
```

Expected:
- First test FAILS because `clause_key_score >= 96` can still accept weak low-coverage matches by key alone.
- Second test FAILS because critical token conflict is not score-capped by matcher guard.

- [ ] **Step 3: Add guard-aware score caps**

In `_weighted_score()` before the final `return`, insert this block after the existing weak numeric cap:

```python
        risk_flags = set(details.get("matcher_risk_flags", []))
        if SAME_KEY_LOW_BODY_COVERAGE in risk_flags:
            weighted = min(weighted, 82.0)
        if (
            CRITICAL_TOKEN_CONFLICT in risk_flags
            and self._alignment_number(details.get("alignment"), "critical_token_overlap", default=1.0) <= 0.0
            and details["body_score"] < 85
        ):
            weighted = min(weighted, 84.0)
        if (
            SAME_NUMBER_LOW_BODY_SIMILARITY in risk_flags
            and details["title_score"] < 80
        ):
            weighted = min(weighted, 78.0)
        if BODY_ONLY_ALIGNMENT_RISK in risk_flags:
            weighted = min(weighted, 84.0)
```

- [ ] **Step 4: Add guard-aware acceptance rules**

In `_candidate_acceptable()`, insert this block immediately before:

```python
        if details["clause_key_score"] >= 96 and details["body_score"] >= 35:
            return True
```

New block:

```python
        risk_flags = set(details.get("matcher_risk_flags", []))
        if (
            SAME_KEY_LOW_BODY_COVERAGE in risk_flags
            and details["body_score"] < 55
            and details["title_score"] < 80
        ):
            return False
        if (
            SAME_NUMBER_LOW_BODY_SIMILARITY in risk_flags
            and details["title_score"] < 80
            and candidate.score < self.low_confidence_review_threshold
        ):
            return False
```

- [ ] **Step 5: Run focused guard tests**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py::test_low_coverage_same_key_with_weak_body_and_title_is_not_accepted_by_key_only tests/test_matcher_optimization.py::test_critical_token_conflict_caps_score_but_keeps_modify_candidate -v
```

Expected: PASS.

- [ ] **Step 6: Run matcher optimization tests**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add backend/app/services/matcher.py backend/tests/test_matcher_optimization.py
git commit -m "feat: apply risk aware matcher guards"
```

## Task 3: Confidence And Method Risk Tests

**Files:**
- Modify: `backend/tests/test_matcher_optimization.py`
- Modify: `backend/app/services/matcher.py`

- [ ] **Step 1: Write failing tests for same-number and body-only risk confidence**

Append these tests near other matcher confidence tests in `backend/tests/test_matcher_optimization.py`:

```python
def test_same_number_low_body_similarity_is_low_confidence_guarded_match() -> None:
    original = [
        clause(
            "O001",
            "5.1",
            "验收",
            "甲方应在系统上线后十个工作日内完成验收并出具书面验收意见。",
        )
    ]
    compare = [
        clause(
            "N001",
            "5.1",
            "违约责任",
            "乙方逾期交付的,应按合同总价每日千分之一向甲方支付违约金。",
        )
    ]

    pair = ClauseMatcher().match(original, compare)[0]

    assert pair.compare is not None
    assert "SAME_NUMBER_LOW_BODY_SIMILARITY" in pair.score_details["matcher_risk_flags"]
    assert pair.match_confidence == "LOW"
    assert pair.match_method in {"same_clause_no_low_similarity", "same_clause_no_weighted"}


def test_body_only_alignment_risk_is_low_confidence() -> None:
    original = [
        clause(
            "O001",
            "",
            "付款",
            "甲方应在2026年6月30日前支付人民币1000元,逾期应承担违约责任。",
        )
    ]
    compare = [
        clause(
            "N001",
            "",
            "结算",
            "甲方应在2027年7月31日前支付人民币5000元,逾期应承担违约责任。",
        )
    ]

    pair = ClauseMatcher(threshold=70).match(original, compare)[0]

    assert pair.compare is not None
    assert pair.match_method == "body_weighted_similarity"
    assert "BODY_ONLY_ALIGNMENT_RISK" in pair.score_details["matcher_risk_flags"]
    assert "CRITICAL_TOKEN_CONFLICT" in pair.score_details["matcher_risk_flags"]
    assert pair.match_confidence == "LOW"
```

- [ ] **Step 2: Run tests to verify failures**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py::test_same_number_low_body_similarity_is_low_confidence_guarded_match tests/test_matcher_optimization.py::test_body_only_alignment_risk_is_low_confidence -v
```

Expected: FAIL if confidence does not read `matcher_risk_flags` or if body-only risk is not tagged.

- [ ] **Step 3: Make confidence read matcher guard flags**

In `_match_confidence()`, insert this block immediately after `details = candidate.details`:

```python
        if details.get("matcher_risk_flags"):
            return "LOW"
```

Keep the existing alignment-risk low-confidence branch after this new branch.

- [ ] **Step 4: Run focused confidence tests**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py::test_same_number_low_body_similarity_is_low_confidence_guarded_match tests/test_matcher_optimization.py::test_body_only_alignment_risk_is_low_confidence -v
```

Expected: PASS.

- [ ] **Step 5: Run all matcher tests**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add backend/app/services/matcher.py backend/tests/test_matcher_optimization.py
git commit -m "feat: lower confidence for guarded matcher risks"
```
