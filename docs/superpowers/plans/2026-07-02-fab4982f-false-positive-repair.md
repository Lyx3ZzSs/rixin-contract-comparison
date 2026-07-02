# fab4982f False Positive Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce false positives confirmed in task `fab4982f-af31-47ee-9fd1-bc8e4064264f` without suppressing real amount, payment-method, dispute-method, signing-date, seal, or signature differences.

**Architecture:** Keep the fix in the diff quality layer. Add narrow suppression/reclassification rules that use page text/evidence geometry and clause structure, so OCR/title gaps and page-edge annotations are handled after extraction while preserving the existing extraction pipeline.

**Tech Stack:** Python, FastAPI backend models, `DiffQualityProcessor`, `ClauseBoundaryCoverageFilter`, pytest, ruff.

---

## File Structure

- Modify: `backend/app/services/diff_quality.py`
  - Add clause-body noise detection for `MODIFY` diffs where only page numbers, edge annotations, or signature/footer fragments changed.
  - Add title-only OCR-gap review/suppression helpers for missing chapter headings.
  - Keep protection checks for amount, dates, contract numbers, payment/dispute choices, seal/signature regions.

- Modify: `backend/app/services/diff/boundary_coverage.py`
  - Extend opposite-page coverage from `ADD/DELETE` to safe `MODIFY` title/heading diffs.
  - Add safe heading coverage checks for short headings like `服务期限与进度要求`, `合同价格及支付`, `不可抗力`, `违约责任`.
  - Keep `_safe_for_page_text_coverage` blocking amounts, dates, percentages, contract numbers, and credit codes.

- Modify: `backend/tests/test_text_cleaning_quality.py`
  - Add tests reproducing representative false positives from pages 2, 3, 7, 24-27, 41, and 44.
  - Add guard tests proving real differences remain.

---

### Task 1: Suppress Page/Footer Annotation Noise Inside `MODIFY` Clause Diffs

**Files:**
- Modify: `backend/tests/test_text_cleaning_quality.py`
- Modify: `backend/app/services/diff_quality.py`

- [ ] **Step 1: Add failing tests for page number + edge annotation diffs**

Append these tests near the existing edge annotation tests in `backend/tests/test_text_cleaning_quality.py`:

```python
def test_diff_quality_suppresses_page_footer_annotation_even_with_long_original_context() -> None:
    diff = DiffItem(
        diff_id="D063",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="4.4",
        title="因乙方未履行安全管理责任",
        original_text="4.4 因乙方未履行安全管理责任。\n—23—\n担全部法律责任。",
        compare_text="4.4 因乙方未履行安全管理责任。\n-23-\n黄科",
        original_snippet="—23—担全部法律责任",
        compare_snippet="-23-黄科",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        quality_status="NEEDS_REVIEW",
        original_evidence=[
            EvidenceBox(page_no=24, bbox=BBox(x0=288, y0=784, x1=320, y1=800), text="—23—"),
            EvidenceBox(page_no=25, bbox=BBox(x0=65, y0=75, x1=178, y1=91), text="担全部法律责任"),
        ],
        compare_evidence=[
            EvidenceBox(page_no=24, bbox=BBox(x0=258, y0=777, x1=284, y1=793), text="-23-"),
            EvidenceBox(page_no=24, bbox=BBox(x0=448, y0=775, x1=502, y1=813), text="黄科"),
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D063"
        and decision.detail["reason"] == "page_number_edge_annotation_noise"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_signature_footer_noise_inside_clause() -> None:
    diff = DiffItem(
        diff_id="D074",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="15.3",
        title="在争议解决期间,合同中未涉及争议部分的条款仍须履行。",
        original_text="15.3 在争议解决期间,合同中未涉及争议部分的条款仍须履行。\n参与人员:",
        compare_text="15.3 在争议解决期间,合同中未涉及争议部分的条款仍须履行。\n共评静\n44意\n黄科",
        original_snippet="参与人员:",
        compare_snippet="共评静44意黄科",
        structural_flags=["PUNCTUATED_HEADING"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        quality_status="NEEDS_REVIEW",
        compare_evidence=[
            EvidenceBox(page_no=34, bbox=BBox(x0=135, y0=439, x1=244, y1=488), text="共评静"),
            EvidenceBox(page_no=34, bbox=BBox(x0=99, y0=778, x1=154, y1=821), text="44意"),
            EvidenceBox(page_no=34, bbox=BBox(x0=439, y0=783, x1=494, y1=821), text="黄科"),
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D074"
        and decision.detail["reason"] == "edge_annotation_clause_noise"
        for decision in result.decisions
    )
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_page_footer_annotation_even_with_long_original_context backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_signature_footer_noise_inside_clause -q
```

Expected: both tests fail because current rules do not suppress `D063`-style long-context page/footer noise or `D074`-style signature/footer fragments.

- [ ] **Step 3: Implement minimal diff-quality helper changes**

In `backend/app/services/diff_quality.py`, update `_looks_like_page_number_edge_annotation_noise` so it evaluates the changed snippets after removing page markers and ignores non-business residual footer text when the relevant evidence is near the page edge.

Add these helpers near the existing edge annotation helpers:

```python
    def _changed_evidence_texts_near_page_edge(self, diff: DiffItem) -> list[str]:
        texts: list[str] = []
        for evidence in [*diff.original_evidence, *diff.compare_evidence]:
            bbox = evidence.bbox
            if bbox.y0 >= 760 or bbox.x0 <= 30 or bbox.x0 >= 390:
                texts.append(evidence.text or "")
        return texts

    def _changed_text_is_footer_annotation_residual(self, text: str) -> bool:
        compact = self._compact(self._remove_page_number_markers(text))
        if not compact:
            return True
        if len(compact) <= 8 and not self.business_token_pattern.search(compact):
            return True
        return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z#]{1,8}", compact))
```

Then adjust `_looks_like_page_number_edge_annotation_noise`:

```python
        if self._changed_evidence_is_near_page_edge(diff):
            edge_text = "".join(self._changed_evidence_texts_near_page_edge(diff))
            return self._changed_text_is_footer_annotation_residual(edge_text or f"{original}{compare}")
```

And adjust `_looks_like_edge_annotation_clause_noise`:

```python
        changed = self._compact(self._changed_text(diff))
        if self._changed_text_has_business_token(diff):
            return False
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z#\d]{1,12}", changed):
            return self._changed_evidence_is_near_page_edge(diff)
        edge_text = self._compact("".join(self._changed_evidence_texts_near_page_edge(diff)))
        return bool(edge_text and len(edge_text) <= 12 and self._changed_evidence_is_near_page_edge(diff))
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_page_footer_annotation_even_with_long_original_context backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_signature_footer_noise_inside_clause backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_real_amount_uppercase_change_with_same_numeric_value -q
```

Expected: all three pass.

---

### Task 2: Suppress Safe Heading Adds When Opposite Page Already Contains the Heading

**Files:**
- Modify: `backend/tests/test_text_cleaning_quality.py`
- Modify: `backend/app/services/diff/boundary_coverage.py`

- [ ] **Step 1: Add failing tests for OCR-missed headings**

Append these tests near `test_diff_quality_suppresses_short_clause_delete_covered_by_opposite_page_text`:

```python
def test_diff_quality_suppresses_heading_add_covered_by_original_page_text() -> None:
    compare_clause = _quality_clause(
        "NC111",
        "服务期限与进度要求",
        side_prefix="N",
        order_index=12,
        page_no=3,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    original_document = _quality_document(
        3,
        "乙方应按合同约定向甲方提供以下技术服务:\n"
        "3. 服务期限与进度要求\n"
        "3.1 乙方提供服务的期限为合同签订后一年。\n"
        "4. 合同价格及支付",
    )
    diff = DiffItem(
        diff_id="D111",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC111",
        title="服务期限与进度要求",
        compare_text="服务期限与进度要求",
        compare_snippet="服务期限与进度要求",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        compare_evidence=compare_clause.bboxes,
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process(
        [diff],
        compare_clauses=[compare_clause],
        original_document=original_document,
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D111"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_keeps_real_dispute_method_change_not_heading_coverage() -> None:
    original_document = _quality_document(
        11,
        "15.2 若争议经协商仍无法解决的,按以下第一种方式处理:\n方式一:诉讼。",
    )
    diff = DiffItem(
        diff_id="D050",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="15.2",
        title="若争议经协商仍无法解决的,按以下第二种方式处理:",
        original_text="15.2 若争议经协商仍无法解决的,按以下第一种方式处理:",
        compare_text="15.2 若争议经协商仍无法解决的,按以下第二种方式处理:",
        original_snippet="一",
        compare_snippet="二",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D050"]
```

- [ ] **Step 2: Run tests and verify the heading test fails**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_heading_add_covered_by_original_page_text backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_real_dispute_method_change_not_heading_coverage -q
```

Expected: heading test fails, dispute-method guard passes.

- [ ] **Step 3: Extend safe page coverage for headings**

In `backend/app/services/diff/boundary_coverage.py`, update `_eligible_page_text_coverage_diff` to allow safe heading adds:

```python
def _eligible_page_text_coverage_diff(diff: DiffItem) -> bool:
    if diff.source_type != "clause":
        return False
    flags = set(diff.review_flags) | set(diff.structural_flags)
    if diff.diff_type in {"ADD", "DELETE"}:
        return bool(
            flags
            & {
                "READING_ORDER_REPAIRED",
                "READING_ORDER_RISK",
                "SHORT_CLAUSE_MATCH_REVIEW",
                "PUNCTUATED_HEADING",
                "TEXT_FOUND_IN_OTHER_CLAUSE",
                "POSSIBLE_SEGMENTATION_DRIFT",
                "POSSIBLE_SPLIT_CLAUSE",
                "POSSIBLE_MERGED_CLAUSE",
            }
        )
    return False
```

If this function already exists with similar content, only add `READING_ORDER_REPAIRED` and `READING_ORDER_RISK` to the allowed set for `ADD/DELETE`; do not allow arbitrary `MODIFY` in this task.

Then update `_safe_for_page_text_coverage` with heading-specific allowance:

```python
    if len(changed_key) < 3:
        return False
    normalized = unicodedata.normalize("NFKC", text or "")
    if re.search(r"(¥|￥|元|万元|亿元|%|％|‰|统一社会信用代码|合同编号)", normalized):
        return False
    if re.search(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", normalized):
        return False
    if re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", normalized):
        return False
    return len(changed_key) <= 120
```

Do not add a hardcoded list of headings. The condition should be based on opposite-page text coverage and safe-value filtering.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_heading_add_covered_by_original_page_text backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_real_dispute_method_change_not_heading_coverage -q
```

Expected: both pass.

---

### Task 3: Suppress Safe `MODIFY` Heading/Layer Mismatch When Both Sides Contain Both Headings

**Files:**
- Modify: `backend/tests/test_text_cleaning_quality.py`
- Modify: `backend/app/services/diff/boundary_coverage.py`

- [ ] **Step 1: Add failing test for `D078` layer mismatch**

Append:

```python
def test_diff_quality_suppresses_heading_layer_mismatch_when_both_pages_contain_both_headings() -> None:
    original_document = _quality_document(
        41,
        "第三章 合同范围\n3.1 服务范围\n本合同项目主要服务范围包括:随县鹿鹤光伏电站功率预测系统授权服务。",
    )
    compare_document = _quality_document(
        41,
        "第三章 合同范围\n3.1 服务范围\n本合同项目主要服务范围包括:随县鹿鹤光伏电站功率预测系统授权服务。",
    )
    diff = DiffItem(
        diff_id="D078",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="第三章",
        title="服务范围",
        original_text="第三章 合同范围",
        compare_text="3.1 服务范围",
        original_snippet="第三章 合同范围",
        compare_snippet="3.1 服务范围",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=[
            "BUSINESS_TOKEN_MISMATCH_REVIEW",
            "LOW_CONFIDENCE_MATCH",
            "POSSIBLE_CLAUSE_MISMATCH",
            "READING_ORDER_RISK",
            "OCR_REMEDIATION_PLANNED",
            "CRITICAL_VALUE_CHANGE",
        ],
        original_evidence=[EvidenceBox(page_no=41, bbox=BBox(x0=218, y0=123, x1=286, y1=148), text="第三章")],
        compare_evidence=[EvidenceBox(page_no=41, bbox=BBox(x0=87, y0=212, x1=103, y1=236), text="3.1")],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_document=original_document,
        compare_document=compare_document,
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D078"
        and decision.detail["reason"] == "heading_layer_mismatch_covered_by_both_pages"
        for decision in result.decisions
    )
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_heading_layer_mismatch_when_both_pages_contain_both_headings -q
```

Expected: FAIL because `MODIFY` heading layer coverage is not implemented.

- [ ] **Step 3: Implement a narrow boundary coverage rule**

In `backend/app/services/diff/boundary_coverage.py`, add this check before `_changed_fragments_covered_by_neighbor_clauses` in `_suppression_reason`:

```python
        if self._heading_layer_mismatch_covered_by_both_pages(diff, context):
            return "heading_layer_mismatch_covered_by_both_pages"
```

Add this method:

```python
    def _heading_layer_mismatch_covered_by_both_pages(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection({"LOW_CONFIDENCE_MATCH", "POSSIBLE_CLAUSE_MISMATCH", "READING_ORDER_RISK"}):
            return False
        if _contains_protected_value(diff.original_snippet) or _contains_protected_value(diff.compare_snippet):
            return False
        original_key = normalize_for_coverage(diff.original_snippet or diff.original_text)
        compare_key = normalize_for_coverage(diff.compare_snippet or diff.compare_text)
        if not original_key or not compare_key:
            return False
        if len(original_key) > 40 or len(compare_key) > 40:
            return False
        original_pages = self._candidate_pages_for_modify_side(diff, "original")
        compare_pages = self._candidate_pages_for_modify_side(diff, "compare")
        return (
            self._document_pages_contain_all(context.original_document, original_pages, {original_key, compare_key})
            and self._document_pages_contain_all(context.compare_document, compare_pages, {original_key, compare_key})
        )
```

Add helper methods:

```python
    def _candidate_pages_for_modify_side(self, diff: DiffItem, side: str) -> set[int]:
        evidence = diff.original_evidence if side == "original" else diff.compare_evidence
        return {item.page_no for item in evidence}

    def _document_pages_contain_all(self, document: Document | None, pages: set[int], keys: set[str]) -> bool:
        if document is None or not pages:
            return False
        search_pages = {page_no + offset for page_no in pages for offset in (-1, 0, 1)}
        for page in document.pages:
            if page.page_no not in search_pages:
                continue
            page_key = normalize_for_coverage("\n".join(block.text for block in page.blocks if block.text))
            if all(key in page_key for key in keys):
                return True
        return False
```

Add protected value helper near `_safe_for_page_text_coverage`:

```python
def _contains_protected_value(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "")
    return bool(
        re.search(r"(¥|￥|元|万元|亿元|%|％|‰|统一社会信用代码|合同编号)", normalized)
        or re.search(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", normalized)
        or re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", normalized)
    )
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_heading_layer_mismatch_when_both_pages_contain_both_headings backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_real_amount_uppercase_change_with_same_numeric_value -q
```

Expected: both pass.

---

### Task 4: Reclassify Mixed Real Difference + Noise Instead of Suppressing the Whole Diff

**Files:**
- Modify: `backend/tests/test_text_cleaning_quality.py`
- Modify: `backend/app/services/diff_quality.py`

- [ ] **Step 1: Add tests for mixed diffs**

Append:

```python
def test_diff_quality_keeps_reference_punctuation_change_but_trims_edge_annotation() -> None:
    diff = DiffItem(
        diff_id="D082",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="(7)",
        title="《电力二次系统安全防护总体方案》国家电力监管委员会电监安全",
        original_text="会电监安全(2006)34号。",
        compare_text="会电监安全〔2006〕34号。\n黄科",
        original_snippet="(2006)34",
        compare_snippet="〔2006〕34黄科",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=[
            EvidenceBox(page_no=44, bbox=BBox(x0=141, y0=312, x1=201, y1=335), text="〔2006〕34"),
            EvidenceBox(page_no=44, bbox=BBox(x0=423, y0=783, x1=484, y1=822), text="黄科"),
        ],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D082"]
    assert result.diffs[0].compare_snippet == "〔2006〕34"
    assert any(
        decision.action == "trimmed_edge_annotation_noise"
        and decision.diff_id == "D082"
        for decision in result.decisions
    )


def test_diff_quality_keeps_slash_unit_format_change_but_not_amount_change() -> None:
    diff = DiffItem(
        diff_id="D068",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="6.3",
        title="乙方发生生产安全人身死亡责任事故的,应按照100万元",
        original_text="应按照100万元/人次的标准向甲方支付违约金。",
        compare_text="应按照100万元\n人次的标准向甲方支付违约金。",
        original_snippet="/",
        compare_snippet="",
        review_flags=[
            "CRITICAL_FIELD_AMOUNT_CHANGE",
            "CRITICAL_FIELD_CHANGE",
            "EVIDENCE_UNRELIABLE",
            "OCR_REMEDIATION_PLANNED",
            "CRITICAL_VALUE_CHANGE",
        ],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D068"]
    assert "CRITICAL_FIELD_AMOUNT_CHANGE" not in result.diffs[0].review_flags
    assert "UNIT_FORMAT_CHANGE_REVIEW" in result.diffs[0].review_flags
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_reference_punctuation_change_but_trims_edge_annotation backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_slash_unit_format_change_but_not_amount_change -q
```

Expected: both fail before implementation.

- [ ] **Step 3: Implement mixed-diff trimming/reclassification**

In `backend/app/services/diff_quality.py`, add a classification step before `_has_critical_field_change(diff)`:

```python
            if self._trim_edge_annotation_from_mixed_diff(diff):
                decisions.append(
                    DiffQualityDecision(
                        action="trimmed_edge_annotation_noise",
                        diff_id=diff.diff_id,
                        detail={"reason": "mixed_clause_edge_annotation"},
                    )
                )
            if self._reclassify_unit_separator_change(diff):
                decisions.append(
                    DiffQualityDecision(
                        action="unit_separator_change_reclassified",
                        diff_id=diff.diff_id,
                        detail={"reason": "slash_unit_separator_only"},
                    )
                )
```

Add helpers:

```python
    def _trim_edge_annotation_from_mixed_diff(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        edge_texts = self._changed_evidence_texts_near_page_edge(diff)
        if not edge_texts:
            return False
        changed = diff.compare_snippet or diff.compare_text
        updated = changed
        for text in edge_texts:
            compact = self._compact(text)
            if compact and len(compact) <= 4 and not self.business_token_pattern.search(compact):
                updated = updated.replace(text, "")
        updated = updated.strip()
        if updated and updated != changed:
            diff.compare_snippet = updated
            diff.compare_text = diff.compare_text.replace(changed, updated) if changed in diff.compare_text else diff.compare_text
            return True
        return False

    def _reclassify_unit_separator_change(self, diff: DiffItem) -> bool:
        original = unicodedata.normalize("NFKC", diff.original_text or "")
        compare = unicodedata.normalize("NFKC", diff.compare_text or "")
        if not re.search(r"\d+\s*万元\s*/\s*人次", original):
            return False
        if not re.search(r"\d+\s*万元\s*人次", compare):
            return False
        if self._compact(original.replace("/", "")) != self._compact(compare):
            return False
        self._remove_flag(diff, "CRITICAL_FIELD_AMOUNT_CHANGE")
        self._remove_flag(diff, "CRITICAL_VALUE_CHANGE")
        self._add_flag(diff, "UNIT_FORMAT_CHANGE_REVIEW")
        diff.quality_status = "NEEDS_REVIEW"
        return True
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_reference_punctuation_change_but_trims_edge_annotation backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_slash_unit_format_change_but_not_amount_change -q
```

Expected: both pass.

---

### Task 5: Full Regression and Artifact-Level Spot Check

**Files:**
- No code changes unless a regression fails.

- [ ] **Step 1: Run focused quality tests**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py -q
```

Expected: all tests in the file pass.

- [ ] **Step 2: Run related backend tests**

Run:

```bash
uv run pytest backend/tests/test_table_compare_structured.py backend/tests/test_matcher_optimization.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run lint and syntax checks**

Run:

```bash
uv run ruff check backend/app/services/diff_quality.py backend/app/services/diff/boundary_coverage.py backend/tests/test_text_cleaning_quality.py
uv run python -m compileall backend/app backend/tests
```

Expected: ruff prints `All checks passed!`; compileall exits 0.

- [ ] **Step 4: Run full backend test suite**

Run:

```bash
uv run pytest backend/tests -q
```

Expected: all backend tests pass.

- [ ] **Step 5: Re-score representative `fab4982f` diffs with current code**

Run a small local script manually or in a temporary REPL to load `storage/tasks/fab4982f-af31-47ee-9fd1-bc8e4064264f/task.json`, construct `DiffItem` objects, construct `Document` page text from `ppocrv5_raw.json`, and pass them through `DiffQualityProcessor`.

Expected:
- Suppressed: `D030`, `D039`, `D042`, `D046`, `D051`, `D053`, `D063-D067`, `D071-D072`, `D074`, `D078`, `D091`, `D093-D094`, `D111-D122`.
- Kept: `D009`, `D014`, `D032`, `D036`, `D050`, `D068`, `D073`, `D082`, `D090`.
- Reclassified or trimmed, not suppressed: `D068`, `D082`, `D090`.

- [ ] **Step 6: Check git diff**

Run:

```bash
git diff -- backend/app/services/diff_quality.py backend/app/services/diff/boundary_coverage.py backend/tests/test_text_cleaning_quality.py
git diff --check
```

Expected: only intended files are modified and `git diff --check` has no output.

---

## Self-Review

- Spec coverage: The plan covers the confirmed false-positive root causes: OCR-missed headings, page/footer annotations inside clauses, heading layer mismatch, mixed real difference plus noise, and true-difference guard rails.
- Placeholder scan: No `TBD`, `TODO`, or open-ended implementation steps remain.
- Type consistency: All tests use existing `DiffItem`, `EvidenceBox`, `BBox`, `_quality_clause`, `_quality_document`, and `DiffQualityProcessor` APIs from `backend/tests/test_text_cleaning_quality.py`.
