# Precision Phase 2B Risk Aware Matcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a risk-aware matcher guard layer that lowers overconfident risky clause matches and exposes the guard decisions to quality attribution.

**Architecture:** Keep the current `ClauseMatcher` scoring pipeline and add a private guard helper that writes `score_details.matcher_risk_flags` plus `score_details.matcher_guard_applied`. Then use those flags in score capping, candidate acceptance, match confidence, and Phase 2A attribution reporting without changing public API models.

**Tech Stack:** Python 3, FastAPI backend service modules, pytest, existing quality regression scripts, markdown docs in Chinese.

---

## File Structure

- Modify `backend/app/services/matcher.py`
  - Add private matcher guard helpers near `_score_details()`.
  - Write `matcher_risk_flags` and `matcher_guard_applied` into every candidate `score_details`.
  - Apply guarded score caps and low-confidence rules.
- Modify `backend/scripts/analyze_quality_attribution.py`
  - Read `score_details.matcher_risk_flags` from `clause_matches.json`.
  - Add per-case and aggregate `matcher_risk_flag_counts`.
  - Include matcher guard flags in suspicious match output and attribution tags.
- Modify `backend/tests/test_matcher_optimization.py`
  - Add focused matcher tests for same-key low coverage, critical token conflict, same-number low body similarity, and body-only alignment risk.
- Modify `backend/tests/test_quality_attribution.py`
  - Add attribution tests for matcher guard flags and malformed matcher flag shapes.
- Create `docs/precision_risk_aware_matcher_workflow.md`
  - Chinese workflow document explaining when guard flags appear, how to run regression, and how to turn real debug artifacts into safe gold cases.

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

## Task 4: Attribution Reads Matcher Risk Flags

**Files:**
- Modify: `backend/tests/test_quality_attribution.py`
- Modify: `backend/scripts/analyze_quality_attribution.py`

- [ ] **Step 1: Write failing attribution test**

Append this test before `test_write_attribution_report_writes_quality_attribution_json()` in `backend/tests/test_quality_attribution.py`:

```python
def test_analyze_run_dir_counts_matcher_risk_flags(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(run_dir / "debug" / "case_a" / "match_matrix_summary.json", {})
    _write_json(
        run_dir / "debug" / "case_a" / "clause_matches.json",
        [
            {
                "original_clause_id": "O001",
                "compare_clause_id": "N001",
                "match_method": "same_clause_key_weighted",
                "match_confidence": "LOW",
                "score_details": {
                    "matcher_risk_flags": [
                        "SAME_KEY_LOW_BODY_COVERAGE",
                        "CRITICAL_TOKEN_CONFLICT",
                    ],
                    "matcher_guard_applied": 1.0,
                    "body_length_coverage": 0.42,
                    "alignment": {
                        "body_similarity": 0.44,
                        "critical_token_overlap": 0.0,
                        "risk_flags": ["CRITICAL_TOKEN_MISMATCH"],
                    },
                },
            },
            {
                "original_clause_id": "O002",
                "compare_clause_id": "N002",
                "match_method": "body_weighted_similarity",
                "match_confidence": "LOW",
                "score_details": {
                    "matcher_risk_flags": ["BODY_ONLY_ALIGNMENT_RISK"],
                    "matcher_guard_applied": 1.0,
                    "alignment": {"risk_flags": ["TEXT_MATCH_NUMBER_MISMATCH"]},
                },
            },
        ],
    )

    report = analyze_run_dir(run_dir)

    case = report["cases"][0]
    assert case["matcher_risk_flag_counts"] == {
        "SAME_KEY_LOW_BODY_COVERAGE": 1,
        "CRITICAL_TOKEN_CONFLICT": 1,
        "BODY_ONLY_ALIGNMENT_RISK": 1,
    }
    assert report["aggregate"]["matcher_risk_flag_counts"] == case["matcher_risk_flag_counts"]
    assert case["suspicious_matches"][0]["matcher_risk_flags"] == [
        "SAME_KEY_LOW_BODY_COVERAGE",
        "CRITICAL_TOKEN_CONFLICT",
    ]
    assert "SAME_KEY_LOW_BODY_COVERAGE" in case["attribution_tags"]
    assert "CRITICAL_TOKEN_CONFLICT" in case["attribution_tags"]
    assert "BODY_ONLY_ALIGNMENT_RISK" in case["attribution_tags"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py::test_analyze_run_dir_counts_matcher_risk_flags -v
```

Expected: FAIL because the report does not include `matcher_risk_flag_counts`.

- [ ] **Step 3: Add matcher risk count extraction**

In `_analyze_case()`, after `risk_counts = _alignment_risk_flag_counts(summary, matches)`, add:

```python
    matcher_risk_counts = _matcher_risk_flag_counts(summary, matches)
```

Pass it into `_attribution_tags()`:

```python
        matcher_risk_counts=matcher_risk_counts,
```

Add it to the returned case object after `alignment_risk_flag_counts`:

```python
        "matcher_risk_flag_counts": dict(matcher_risk_counts),
```

Add this function after `_alignment_risk_flag_counts()`:

```python
def _matcher_risk_flag_counts(
    summary: dict[str, Any],
    matches: list[Any],
) -> Counter[str]:
    summary_counts = _counter_from_mapping(summary.get("matcher_risk_flag_counts"))
    if summary_counts:
        return summary_counts

    counts: Counter[str] = Counter()
    for match in matches:
        counts.update(_matcher_risk_flags_from_match(match))
    return counts
```

Add this helper after `_risk_flags_from_match()`:

```python
def _matcher_risk_flags_from_match(match: Any) -> list[str]:
    if not isinstance(match, dict):
        return []
    score_details = match.get("score_details")
    if not isinstance(score_details, dict):
        return []
    raw_flags = score_details.get("matcher_risk_flags")
    if not isinstance(raw_flags, list | tuple | set):
        return []
    return [flag for flag in raw_flags if isinstance(flag, str) and flag]
```

- [ ] **Step 4: Add matcher flags to suspicious matches**

In `_suspicious_matches()`, after:

```python
        risk_flags = _risk_flags_from_match(match)
```

add:

```python
        matcher_risk_flags = _matcher_risk_flags_from_match(match)
```

Change the `_is_suspicious_match(...)` call to:

```python
        if not _is_suspicious_match(method, confidence, risk_flags, matcher_risk_flags):
            continue
```

Add this field to each suspicious item:

```python
                "matcher_risk_flags": matcher_risk_flags,
```

Change `_is_suspicious_match()` signature and body to:

```python
def _is_suspicious_match(
    method: str,
    confidence: str,
    risk_flags: list[str],
    matcher_risk_flags: list[str],
) -> bool:
    return (
        confidence == "LOW"
        or bool(risk_flags)
        or bool(matcher_risk_flags)
        or method in SUSPICIOUS_MATCH_METHODS
    )
```

- [ ] **Step 5: Add matcher flags to attribution tags and aggregate report**

Change `_attribution_tags()` signature to include:

```python
    matcher_risk_counts: Counter[str],
```

Inside `_attribution_tags()`, after `tags.update(_raw_risk_flag_tags(risk_counts))`, add:

```python
    tags.update(_raw_matcher_risk_flag_tags(matcher_risk_counts))
```

Add this helper after `_raw_risk_flag_tags()`:

```python
def _raw_matcher_risk_flag_tags(matcher_risk_counts: Counter[str]) -> set[str]:
    return {
        flag
        for flag in (
            "SAME_KEY_LOW_BODY_COVERAGE",
            "CRITICAL_TOKEN_CONFLICT",
            "SAME_NUMBER_LOW_BODY_SIMILARITY",
            "BODY_ONLY_ALIGNMENT_RISK",
        )
        if matcher_risk_counts.get(flag, 0) > 0
    }
```

In `_aggregate_report()`, add this field after `alignment_risk_flag_counts`:

```python
        "matcher_risk_flag_counts": dict(
            _sum_case_counters(cases, "matcher_risk_flag_counts"),
        ),
```

- [ ] **Step 6: Run attribution focused test**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py::test_analyze_run_dir_counts_matcher_risk_flags -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add backend/scripts/analyze_quality_attribution.py backend/tests/test_quality_attribution.py
git commit -m "feat: attribute matcher risk guards"
```

## Task 5: Attribution Shape Warnings

**Files:**
- Modify: `backend/tests/test_quality_attribution.py`
- Modify: `backend/scripts/analyze_quality_attribution.py`

- [ ] **Step 1: Write failing malformed-shape test**

Append this test before `test_analyze_run_dir_ignores_bool_debug_counts()` in `backend/tests/test_quality_attribution.py`:

```python
def test_analyze_run_dir_warns_for_malformed_matcher_risk_flags(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(run_dir / "debug" / "case_a" / "match_matrix_summary.json", {})
    _write_json(
        run_dir / "debug" / "case_a" / "clause_matches.json",
        [
            {
                "match_method": "same_clause_key_weighted",
                "score_details": {
                    "matcher_risk_flags": "not-a-list",
                    "alignment": {"risk_flags": []},
                },
            }
        ],
    )

    report = analyze_run_dir(run_dir)

    assert report["cases"][0]["matcher_risk_flag_counts"] == {}
    assert any("invalid matcher_risk_flags" in warning for warning in report["cases"][0]["warnings"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py::test_analyze_run_dir_warns_for_malformed_matcher_risk_flags -v
```

Expected: FAIL because malformed `matcher_risk_flags` is silently ignored.

- [ ] **Step 3: Add warning in malformed match validation**

In `_record_malformed_match_warnings()`, after the existing `score_details` shape check and before the `alignment` check, insert:

```python
        matcher_risk_flags = score_details.get("matcher_risk_flags")
        if "matcher_risk_flags" in score_details and not isinstance(matcher_risk_flags, list | tuple | set):
            warnings.append(f"{case_id}: invalid matcher_risk_flags in clause_matches[{index}]")
```

- [ ] **Step 4: Run attribution tests**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add backend/scripts/analyze_quality_attribution.py backend/tests/test_quality_attribution.py
git commit -m "test: warn on malformed matcher risk flags"
```

## Task 6: Chinese Workflow Documentation

**Files:**
- Create: `docs/precision_risk_aware_matcher_workflow.md`

- [ ] **Step 1: Create workflow document**

Create `docs/precision_risk_aware_matcher_workflow.md` with this content:

```markdown
# Precision Phase 2B：风险感知 Matcher 调优工作流

## 目标

本阶段把 Phase 2A 的质量归因结果转化为 matcher guard。它不重写 matcher，也不引入 LLM 复核，而是在现有候选评分后识别高风险匹配，降低过度自信，并把原因写入 debug artifacts。

## 新增输出

每个候选的 `score_details` 会包含：

```json
{
  "matcher_risk_flags": ["SAME_KEY_LOW_BODY_COVERAGE"],
  "matcher_guard_applied": 1.0
}
```

`matcher_risk_flags` 为空时，`matcher_guard_applied` 为 `0.0`。

## 风险标签

| 标签 | 含义 | 默认处理 |
| --- | --- | --- |
| `SAME_KEY_LOW_BODY_COVERAGE` | clause key 相同但正文覆盖不足 | 不允许普通高置信；弱正文且标题不强时不靠 key 接受 |
| `CRITICAL_TOKEN_CONFLICT` | 金额、日期、期限等关键 token 冲突 | `match_confidence=LOW`；关键 token 完全不重合且正文分低时限制分数 |
| `SAME_NUMBER_LOW_BODY_SIMILARITY` | 编号相同但正文相似度过低 | `match_confidence=LOW`；标题不强时限制强匹配 |
| `BODY_ONLY_ALIGNMENT_RISK` | body-only 候选存在 alignment 风险 | `match_confidence=LOW`，保留候选给 attribution 定位 |

## 如何运行质量回归

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2b-final \
  --run-id precision-p2b-final \
  --fail-on-regression

python scripts/analyze_quality_attribution.py \
  --run-dir .ocr-compare-quality/runs/precision-p2b-final
```

重点查看：

- `aggregate.matcher_risk_flag_counts`
- `aggregate.attribution_counts`
- `cases[].suspicious_matches[].matcher_risk_flags`
- `false_positive_count`
- `false_negative_count`
- `precision`
- `recall`
- `evidence_hit_rate`

## 如何使用真实任务 debug artifacts

真实任务 debug artifacts 只用于本地分析，不提交仓库。可查看：

```text
storage/tasks/<task-id>/debug/clause_matches.json
storage/tasks/<task-id>/debug/match_matrix_summary.json
```

如果真实任务发现新的错配模式，先抽象为不含真实合同文本的最小复现，再写入：

- `backend/tests/test_matcher_optimization.py`
- 或 `backend/tests/fixtures/ocr_compare_cases/<case-id>/`

禁止提交：

- 原始合同。
- 包含客户、金额、人员、项目等敏感信息的 debug artifact。
- 未脱敏的 expected/gold case。

## 人工判断建议

- 如果 `matcher_risk_flags` 出现但质量回归通过，说明 guard 正在降低风险候选的置信度，不代表一定是错误匹配。
- 如果 `SAME_KEY_LOW_BODY_COVERAGE` 高频出现，应优先检查 clause splitter 是否把签署页、附件、报价段切得过短。
- 如果 `CRITICAL_TOKEN_CONFLICT` 高频出现，应检查实际 diff 是否已正确暴露金额、日期、期限变化。
- 如果 `BODY_ONLY_ALIGNMENT_RISK` 高频出现，应优先补充模板化合同的 gold case。
```

- [ ] **Step 2: Commit Task 6**

```bash
git add docs/precision_risk_aware_matcher_workflow.md
git commit -m "docs: add risk aware matcher workflow"
```

## Task 7: Final Verification And Review

**Files:**
- Verify only; no new code expected.

- [ ] **Step 1: Run matcher and attribution tests**

Run:

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py tests/test_quality_attribution.py -v
```

Expected: PASS.

- [ ] **Step 2: Run quality regression related tests**

Run:

```bash
cd backend
python -m pytest tests/test_quality_regression.py tests/test_quality_attribution.py -v
```

Expected: PASS. If `tests/test_quality_regression.py` does not exist in this checkout, run this instead:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py -v
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

- [ ] **Step 5: Run quality regression smoke**

Run:

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2b-final \
  --run-id precision-p2b-final \
  --fail-on-regression
```

Expected JSON includes:

```json
{
  "status": "PASSED",
  "run_id": "precision-p2b-final",
  "failed_gates": []
}
```

- [ ] **Step 6: Run quality attribution smoke**

Run:

```bash
cd backend
python scripts/analyze_quality_attribution.py \
  --run-dir .ocr-compare-quality/runs/precision-p2b-final
```

Expected JSON includes:

```json
{
  "status": "PASSED",
  "run_id": "precision-p2b-final"
}
```

Then inspect the generated report:

```bash
cd backend
python -m json.tool .ocr-compare-quality/runs/precision-p2b-final/quality_attribution.json
```

Expected: output contains `aggregate.matcher_risk_flag_counts`.

- [ ] **Step 7: Run full backend test suite**

Run:

```bash
cd backend
python -m pytest
```

Expected: PASS.

- [ ] **Step 8: Check git status**

Run:

```bash
git status --short
```

Expected: only intentional Phase 2B files are modified or committed. Do not stage unrelated local files such as `frontend/src/picture/favicon.ico`, `.agents/`, `backend/.ocr-compare-quality/`, `picture/`, `skills-lock.json`, or `storage/`.

## Self-Review Checklist

- Spec coverage:
  - Same key low coverage: Tasks 1, 2, 4, 6.
  - Critical token conflict: Tasks 1, 2, 3, 4, 6.
  - Same number low body similarity: Tasks 1, 2, 3, 6.
  - Body-only alignment risk: Tasks 1, 3, 4, 6.
  - Section mismatch remains blocked and attributed: existing matcher behavior is protected by existing tests; Task 7 runs all matcher tests.
  - No LLM, no model routing, no front-end workbench: plan touches only matcher, attribution, tests, and docs.
- Placeholder scan:
  - 本计划不包含占位实现、延后实现说明或未指定执行内容。
- Type consistency:
  - `score_details.matcher_risk_flags` is always a list of strings.
  - `score_details.matcher_guard_applied` is always numeric `1.0` or `0.0`.
  - Attribution aggregate field is `matcher_risk_flag_counts`.
