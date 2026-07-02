# E9 Visual False Positive Suppression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suppress the visually confirmed false positives from task `e9debd86-ada8-45d6-a810-bf1185033fc7` without hiding real handwriting, seal, date, amount, or option-selection differences.

**Architecture:** Add conservative post-diff guards to the existing quality pipeline. `ClauseBoundaryCoverageFilter` handles clause/page coverage and structural drift; `DiffQualityProcessor` handles table/form serialization and seal-region OCR artifacts.

**Tech Stack:** Python, Pydantic models in `backend/app/models.py`, existing diff quality services, pytest, ruff.

---

## File Structure

- Modify `backend/app/services/diff/boundary_coverage.py`
  - Add page coverage helpers for non-contiguous snippet coverage.
  - Add structural drift suppression for low-coverage split/contained clause diffs.
  - Keep protected value checks local to coverage helpers.
- Modify `backend/app/services/diff_quality.py`
  - Add low-value suppression for form/table serialization noise.
  - Add low-value suppression for isolated seal/signature OCR fragments.
  - Preserve real handwriting, seal, signature, date, amount, and option changes.
- Modify `backend/tests/test_text_cleaning_quality.py`
  - Add focused failing tests for each confirmed false-positive family.
  - Add protection tests for real differences that must remain.
- Do not create a new script for the task-level spot check; use the inline read-only command in Task 6 to avoid adding maintenance surface.

## Task 1: Page Fragment Coverage For Low-Coverage Clause Drift

**Files:**
- Modify: `backend/app/services/diff/boundary_coverage.py`
- Test: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Add failing tests for D093-like and D113-like coverage**

Append these tests near the existing boundary coverage tests in `backend/tests/test_text_cleaning_quality.py`:

```python
def test_diff_quality_suppresses_non_contiguous_split_original_fragments_covered_by_opposite_page() -> None:
    original_document = _quality_document(
        2,
        "1.9.除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,均包括本数;“不满”“超\n"
        "过”“以外”,不包括本数;“×日前”“×日后”不包括当日。按照日、月、年计算期间\n"
        "的,开始的当日不算入,从下一日开始计算。期间的最后一日法定休假日的,以\n"
        "法定休假日结束的次日为期间的最后一日。\n"
        "2. 服务内容",
    )
    diff = DiffItem(
        diff_id="D093",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="1.9",
        original_clause_id="OC010P01",
        compare_clause_id="NC010",
        original_text="过”“以外”,不包括本数;“×日前”“×日后”不包括当日。按照日、月、年计算期",
        compare_text=(
            "1.9. 除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,均包括本数;“不满”“超\n"
            "过”“以外”,不包括本数;“×日前”“×日后”不包括当日。按照日、月、年计算期\n"
            "间的,开始的当日不算入,从下一日开始计算。期间的最后一日法定休假日的,\n"
            "以法定休假日结束的次日为期间的最后一日。"
        ),
        original_snippet="",
        compare_snippet=(
            "1.9. 除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,均包括本数;"
            "“不满”“超间的,开始的当日不算入,从下一日开始计算。"
            "期间的最后一日法定休假日的,以法定休假日结束的次日为期间的最后一日。"
        ),
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_SPLIT_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[
            EvidenceBox(page_no=2, bbox=BBox(x0=62, y0=626, x1=82, y1=639), text="1.9."),
            EvidenceBox(page_no=2, bbox=BBox(x0=80, y0=626, x1=350, y1=639), text="除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,"),
            EvidenceBox(page_no=2, bbox=BBox(x0=95, y0=672, x1=122, y1=686), text="间的,"),
            EvidenceBox(page_no=2, bbox=BBox(x0=96, y0=695, x1=300, y1=711), text="以法定休假日结束的次日为期间的最后一日。"),
        ],
        match_score_details={"body_length_coverage": 0.2746, "split_original_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D093"
        and decision.detail["reason"] == "low_coverage_split_page_fragment_covered"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_existing_clause_add_cut_by_reading_order() -> None:
    original_document = _quality_document(
        4,
        "4.2.1 双方同意采用以下第（一）、（三）种方式进行付款【注:可多选】:\n"
        "（一）转账/电汇;\n（二）信用证;",
    )
    diff = DiffItem(
        diff_id="D113",
        diff_type="ADD",
        source_type="clause",
        clause_no="4.2.1",
        compare_text="双方同意采用以下第",
        compare_snippet="双方同意采用以下第",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[EvidenceBox(page_no=4, bbox=BBox(x0=120, y0=160, x1=260, y1=180), text="双方同意采用以下第")],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_non_contiguous_split_original_fragments_covered_by_opposite_page backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_existing_clause_add_cut_by_reading_order -q
```

Expected: the D093-like test fails because the current coverage check requires a contiguous normalized string; the D113-like test may pass if current page coverage already handles it. If D113 passes, keep it as regression coverage.

- [ ] **Step 3: Implement fragment coverage helpers**

In `backend/app/services/diff/boundary_coverage.py`, add these helpers near `_page_text_contains_changed_text`:

```python
def _material_fragment_keys(text: str, evidence_texts: list[str] | None = None) -> list[str]:
    raw_fragments: list[str] = []
    raw_fragments.extend(evidence_texts or [])
    raw_fragments.extend(re.split(r"[\n。；;，,]+", text or ""))
    fragments: list[str] = []
    seen: set[str] = set()
    for fragment in raw_fragments:
        key = normalize_for_coverage(fragment)
        if not key or key in seen:
            continue
        if len(key) < 3 and not re.search(r"\d", key):
            continue
        if re.fullmatch(r"\d{1,2}", key):
            continue
        seen.add(key)
        fragments.append(key)
    return fragments


def _page_text_contains_all_fragments(page_text: str, page_lines: list[str], fragments: list[str]) -> bool:
    if not fragments:
        return False
    page_key = normalize_for_coverage(page_text)
    return all(
        fragment in page_key or _page_text_contains_changed_text(page_text, page_lines, fragment)
        for fragment in fragments
    )
```

- [ ] **Step 4: Add structural split coverage suppression**

In `ClauseBoundaryCoverageFilter._suppression_reason`, insert this before `_changed_fragments_covered_by_neighbor_clauses`:

```python
        if self._low_coverage_split_fragments_covered_by_opposite_page(diff, context):
            return "low_coverage_split_page_fragment_covered"
```

Add this method inside `ClauseBoundaryCoverageFilter`:

```python
    def _low_coverage_split_fragments_covered_by_opposite_page(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> bool:
        if diff.source_type != "clause":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection({"LOW_COVERAGE_MATCH_REVIEW", "PARTIAL_CLAUSE_MATCH", "POSSIBLE_SPLIT_CLAUSE", "TEXT_FOUND_IN_OTHER_CLAUSE"}):
            return False
        details = diff.match_score_details or {}
        if details.get("body_length_coverage", 1.0) >= 0.70 and details.get("split_original_clause", 0.0) < 1:
            return False

        has_original = bool((diff.original_snippet or "").strip())
        has_compare = bool((diff.compare_snippet or "").strip())
        if has_original == has_compare:
            return False
        changed = diff.original_snippet if has_original else diff.compare_snippet
        evidence = diff.original_evidence if has_original else diff.compare_evidence
        fragments = _material_fragment_keys(changed, [item.text for item in evidence if item.text])
        if not fragments:
            return False

        opposite_document = context.compare_document if has_original else context.original_document
        if opposite_document is None:
            return False
        pages = self._candidate_pages_for_modify_side(diff, "original" if has_original else "compare")
        if not pages:
            return False
        search_pages = {page_no + offset for page_no in pages for offset in (-1, 0, 1)}
        for page in opposite_document.pages:
            if page.page_no not in search_pages:
                continue
            page_lines = [block.text for block in page.blocks if block.text]
            page_text = "\n".join(page_lines)
            if _page_text_contains_all_fragments(page_text, page_lines, fragments):
                return True
        return False
```

This deliberately allows protected-looking fragments such as dates only when the exact material fragments are covered on the opposite page. Do not suppress two-sided protected value changes; those remain blocked by the one-sided snippet requirement and the protection tests in Task 5.

- [ ] **Step 5: Run the targeted tests and commit**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_non_contiguous_split_original_fragments_covered_by_opposite_page backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_existing_clause_add_cut_by_reading_order -q
```

Expected: both pass.

Commit:

```bash
git add backend/app/services/diff/boundary_coverage.py backend/tests/test_text_cleaning_quality.py
git commit -m "fix(diff): suppress split clause page coverage noise"
```

## Task 2: Short Heading, Metadata Heading, And False Delete Coverage

**Files:**
- Modify: `backend/app/services/diff/boundary_coverage.py`
- Test: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Add failing tests for D011, D094, D098, and D091**

Append:

```python
def test_diff_quality_suppresses_metadata_heading_delete_when_present_on_compare_page() -> None:
    compare_document = _quality_document(2, "1. 定义\n除非另有明确约定,下列词语应具有本条所赋予的含义:")
    diff = DiffItem(
        diff_id="D011",
        diff_type="DELETE",
        source_type="metadata",
        title="封面额外文本",
        original_text="1.",
        original_snippet="1.",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        original_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=100, y0=500, x1=130, y1=520), text="1.")],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_heading_when_opposite_page_has_number_and_body_continuation() -> None:
    original_document = _quality_document(
        2,
        "1.9 条款正文。\n2.\n乙方应按合同约定向甲方提供以下技术服务:\n长源电力随州公司2026年新能源场站功率预测系统授权服务项目。",
    )
    diff = DiffItem(
        diff_id="D094",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="2",
        original_clause_id="OC010P02",
        compare_clause_id="NC011",
        original_text="乙方应按合同约定向甲方提供以下技术服务:",
        compare_text="2. 服务内容\n乙方应按合同约定向甲方提供以下技术服务:",
        original_snippet="",
        compare_snippet="2. 服务内容",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_SPLIT_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="2. 服务内容")],
        match_score_details={"body_length_coverage": 0.1351, "split_original_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "heading_with_bare_number_and_body_covered"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_false_delete_when_text_exists_on_compare_page() -> None:
    compare_document = _quality_document(12, "本特别约定是对合同其他条款的修改或补充。\n（以下无正文）")
    diff = DiffItem(
        diff_id="D098",
        diff_type="DELETE",
        source_type="clause",
        title="(以下无正文)",
        original_text="(以下无正文)",
        original_snippet="(以下无正文)",
        review_flags=["NON_MAIN_CONTRACT_SECTION", "OCR_LOW_CONFIDENCE", "OCR_REMEDIATION_PLANNED"],
        original_evidence=[EvidenceBox(page_no=12, bbox=BBox(x0=170, y0=215, x1=260, y1=235), text="(以下无正文)")],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] in {"changed_text_covered_by_opposite_page_text", "short_heading_text_covered_by_opposite_page"}
        for decision in result.decisions
    )


def test_diff_quality_suppresses_large_false_delete_when_compare_page_contains_fragments() -> None:
    compare_document = _quality_document(
        14,
        "项目名称:长源电力随州公司2026年新能源场站功率预测系统授权服务单一来源项目\n"
        "甲方:国能长源随州发电有限公司随县分公司\n"
        "乙方:国能日新科技股份有限公司\n"
        "协议有效期:2026年7月1日至2027年6月30日\n"
        "为贯彻“安全第一、预防为主、综合治理”的方针。",
    )
    diff = DiffItem(
        diff_id="D091",
        diff_type="MODIFY",
        source_type="clause",
        original_text=(
            "项目名称:长源电力随州公司2026年新能源场站功率预测系统授权服务单一来源项目\n"
            "甲方:国能长源随州发电有限公司随县分公司\n"
            "乙方:国能日新科技股份有限公司\n"
            "协议有效期:2026年7月1日至2027年6月30日\n"
            "为贯彻“安全第一、预防为主、综合治理”的方针。"
        ),
        compare_text="",
        original_snippet=(
            "项目名称:长源电力随州公司2026年新能源场站功率预测系统授权服务单一来源项目"
            "甲方:国能长源随州发电有限公司随县分公司"
            "乙方:国能日新科技股份有限公司"
            "协议有效期:2026年7月1日至2027年6月30日"
        ),
        compare_snippet="",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_MERGED_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        original_evidence=[
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=140, x1=450, y1=170), text="项目名称:长源电力随州公司2026年新能源场站功率预测系统授权服务单一来源项目"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=180, x1=450, y1=210), text="甲方:国能长源随州发电有限公司随县分公司"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=220, x1=450, y1=250), text="乙方:国能日新科技股份有限公司"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=260, x1=450, y1=290), text="协议有效期:2026年7月1日至2027年6月30日"),
        ],
        match_score_details={"body_length_coverage": 0.35, "merged_compare_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "low_coverage_split_page_fragment_covered"
        for decision in result.decisions
    )
```

- [ ] **Step 2: Run tests and verify failures**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_metadata_heading_delete_when_present_on_compare_page backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_heading_when_opposite_page_has_number_and_body_continuation backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_false_delete_when_text_exists_on_compare_page backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_large_false_delete_when_compare_page_contains_fragments -q
```

Expected: D011/D094/D091-like tests fail. D098-like may fail because `NON_MAIN_CONTRACT_SECTION` is not currently eligible for page coverage.

- [ ] **Step 3: Implement heading/body continuation coverage**

In `ClauseBoundaryCoverageFilter._suppression_reason`, insert before `_low_coverage_split_fragments_covered_by_opposite_page`:

```python
        if self._heading_with_bare_number_and_body_covered(diff, context):
            return "heading_with_bare_number_and_body_covered"
```

Add:

```python
    def _heading_with_bare_number_and_body_covered(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        if (diff.original_snippet or "").strip() or not (diff.compare_snippet or "").strip():
            return False
        heading = diff.compare_snippet.strip()
        heading_key = normalize_for_coverage(heading)
        if not _safe_for_heading_number_coverage(heading, heading_key):
            return False
        match = re.match(r"^\s*(\d{1,2})\s*[.．。]\s*(.+)$", heading)
        if not match:
            return False
        parent_no = match.group(1)
        body_key = normalize_for_coverage(diff.original_text)
        if not body_key or len(body_key) < 8:
            return False
        pages = self._candidate_pages_for_modify_side(diff, "compare")
        if not pages:
            return False
        if not self._opposite_pages_have_bare_parent_and_child(context.original_document, pages, parent_no):
            return False
        return self._document_pages_contain_all(context.original_document, pages, {body_key})
```

- [ ] **Step 4: Broaden page coverage eligibility for short non-main text**

Update `_changed_text_covered_by_opposite_page_text` to allow metadata and clause diffs:

```python
        if diff.source_type not in {"clause", "metadata"} or diff.diff_type not in {"ADD", "DELETE"}:
            return False
```

In the same method, allow short numeric headings such as `1.` when a same/neighbor opposite page line contains the number plus heading text:

```python
        changed_key = normalize_for_coverage(changed)
        short_numeric_heading = bool(re.fullmatch(r"\d{1,2}", changed_key))
        if not short_numeric_heading and not _safe_for_page_text_coverage(changed, changed_key):
            return False
```

When searching pages, use the existing `_page_text_contains_changed_text` for short numeric headings:

```python
            if short_numeric_heading:
                if any(
                    normalize_for_coverage(line).startswith(changed_key)
                    and re.search(r"[\u4e00-\u9fff]", line)
                    for line in page_lines
                ):
                    return True
                continue
            if _page_text_contains_changed_text(page_text, page_lines, changed_key):
                return True
```

Update `_eligible_page_text_coverage_diff` so short heading/text deletes with OCR, metadata, or non-main flags can use page coverage:

```python
                "LAYOUT_MISMATCH_RISK",
                "PAGE_UNRELIABLE",
                "NON_MAIN_CONTRACT_SECTION",
                "OCR_LOW_CONFIDENCE",
```

Do not add broad eligibility without the existing `_safe_for_page_text_coverage` check.

- [ ] **Step 5: Run targeted tests and commit**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_metadata_heading_delete_when_present_on_compare_page backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_heading_when_opposite_page_has_number_and_body_continuation backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_false_delete_when_text_exists_on_compare_page backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_large_false_delete_when_compare_page_contains_fragments -q
```

Expected: both pass.

Commit:

```bash
git add backend/app/services/diff/boundary_coverage.py backend/tests/test_text_cleaning_quality.py
git commit -m "fix(diff): cover short headings across split clauses"
```

## Task 3: Form And Table Serialization Noise

**Files:**
- Modify: `backend/app/services/diff_quality.py`
- Test: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Add failing tests for D036, D020, and D023**

Append:

```python
def test_diff_quality_suppresses_form_separator_only_change() -> None:
    diff = DiffItem(
        diff_id="D036",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="(二)",
        original_text="（三）_____/_____费用由乙方承担;",
        compare_text="（三）//费用由乙方承担;",
        original_snippet="(三)费用由乙方承担:",
        compare_snippet="(三)//费用由乙方承担;",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "form_separator_equivalent"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_seal_occluded_signing_label_table_text() -> None:
    diff = DiffItem(
        diff_id="D020",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="甲方（盖章）： 国能长源随州发电有限公司随县分公司 | 乙方（盖章）： 国能日新科技股份有限公司",
        compare_text="甲方 国能长源随州发电有限公司随县分公司 | 国能日新科技股份有限公司",
        review_flags=["TABLE_REGION_REVIEW"],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "seal_occluded_signing_label_covered"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_table_header_serialization_equivalent() -> None:
    diff = DiffItem(
        diff_id="D023",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：封面信息",
        original_text="要求（甲方填写） | 乙方响应",
        compare_text="要求（甲方填写）乙方响应",
        original_snippet="要求（甲方填写） | 乙方响应",
        compare_snippet="要求（甲方填写）乙方响应",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "table_header_serialization_equivalent"
        for decision in result.decisions
    )
```

- [ ] **Step 2: Add protection tests for D021/D022-like real signing additions**

Append:

```python
def test_diff_quality_keeps_signing_table_signature_and_date_additions() -> None:
    signature_diff = DiffItem(
        diff_id="D021",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="法定代表人（负责人）或 授权代表（签字）： | 法定代表人（负责人）或 授权代表（签字）：",
        compare_text="法定负责人 授权代表300240 | 法定代表负责人 或 各商专用 授权代表 22号3",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "TABLE_REGION_REVIEW"],
    )
    date_diff = DiffItem(
        diff_id="D022",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="签订时间: | 签订时间:",
        compare_text="签订时间：2026年5月15日 | 签订时间：2026年5月15日",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "TABLE_REGION_REVIEW"],
    )

    result = DiffQualityProcessor().process([signature_diff, date_diff])

    assert [item.diff_id for item in result.diffs] == ["D021", "D022"]
```

- [ ] **Step 3: Run tests and verify the first three fail and protection passes**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_form_separator_only_change backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_seal_occluded_signing_label_table_text backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_table_header_serialization_equivalent backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_signing_table_signature_and_date_additions -q
```

Expected: suppression tests fail; protection test passes or remains kept after implementation.

- [ ] **Step 4: Implement low-value reasons in `DiffQualityProcessor`**

In `_suppression_reason`, add checks before `_has_critical_field_change(diff)`:

```python
        if self._looks_like_form_separator_equivalent(diff):
            return "form_separator_equivalent"
        if self._looks_like_table_header_serialization_equivalent(diff):
            return "table_header_serialization_equivalent"
        if self._looks_like_seal_occluded_signing_label_covered(diff):
            return "seal_occluded_signing_label_covered"
```

Add methods:

```python
    def _looks_like_form_separator_equivalent(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        original = self._compact(re.sub(r"[/\\_＿—－-]+", "", diff.original_text or diff.original_snippet))
        compare = self._compact(re.sub(r"[/\\_＿—－-]+", "", diff.compare_text or diff.compare_snippet))
        if not original or original != compare:
            return False
        changed = f"{diff.original_snippet}{diff.compare_snippet}"
        return not self.business_token_pattern.search(changed.replace("/", "").replace("\\", ""))

    def _looks_like_table_header_serialization_equivalent(self, diff: DiffItem) -> bool:
        if diff.source_type != "table" or diff.diff_type != "MODIFY":
            return False
        original = self._compact((diff.original_text or diff.original_snippet).replace("|", ""))
        compare = self._compact((diff.compare_text or diff.compare_snippet).replace("|", ""))
        if not original or original != compare:
            return False
        text = f"{diff.original_text} {diff.compare_text}"
        return "要求" in text and "乙方响应" in text

    def _looks_like_seal_occluded_signing_label_covered(self, diff: DiffItem) -> bool:
        if diff.source_type != "table" or diff.diff_type != "MODIFY":
            return False
        text = f"{diff.title} {diff.original_text} {diff.compare_text}"
        if not re.search(r"盖章|甲方|乙方", text):
            return False
        if self._canonical_date(text) or re.search(r"授权代表|签字|签订时间", diff.compare_text or ""):
            return False
        original_companies = _company_name_set(diff.original_text)
        compare_companies = _company_name_set(diff.compare_text)
        return bool(original_companies and compare_companies and original_companies == compare_companies)
```

- [ ] **Step 5: Run targeted tests and commit**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_form_separator_only_change backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_seal_occluded_signing_label_table_text backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_table_header_serialization_equivalent backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_signing_table_signature_and_date_additions -q
```

Expected: all pass.

Commit:

```bash
git add backend/app/services/diff_quality.py backend/tests/test_text_cleaning_quality.py
git commit -m "fix(diff): suppress form and table OCR serialization noise"
```

## Task 4: Seal Artifact OCR Fragment Suppression

**Files:**
- Modify: `backend/app/services/diff_quality.py`
- Test: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Add failing test for D027-like isolated seal artifact**

Append:

```python
def test_diff_quality_suppresses_isolated_seal_region_ocr_fragment() -> None:
    diff = DiffItem(
        diff_id="D027",
        diff_type="ADD",
        source_type="seal",
        title="印章区域（第29页）",
        compare_text="图",
        compare_snippet="图",
        review_flags=[
            "OCR_LOW_CONFIDENCE",
            "PAGE_UNRELIABLE",
            "READING_ORDER_RISK",
            "SEAL_OR_SIGNATURE_RISK",
            "OCR_REMEDIATION_MANUAL_REVIEW",
            "OCR_REMEDIATION_PLANNED",
        ],
        compare_evidence=[EvidenceBox(page_no=29, bbox=BBox(x0=420, y0=300, x1=440, y1=330), text="图")],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "isolated_seal_artifact_text"
        for decision in result.decisions
    )
```

- [ ] **Step 2: Add protection test for real seal text**

Append:

```python
def test_diff_quality_keeps_real_seal_or_signature_region_addition() -> None:
    diff = DiffItem(
        diff_id="D026",
        diff_type="ADD",
        source_type="seal",
        title="印章区域（第29页）",
        compare_text="甲方（盖章） 法定代表人（负责人）/授权代表（签字） 42130130002406",
        compare_snippet="甲方（盖章） 法定代表人（负责人）/授权代表（签字） 42130130002406",
        review_flags=["SEAL_OR_SIGNATURE_RISK", "OCR_REMEDIATION_MANUAL_REVIEW", "OCR_REMEDIATION_PLANNED"],
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D026"]
```

- [ ] **Step 3: Run tests and verify the suppression test fails**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_isolated_seal_region_ocr_fragment backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_real_seal_or_signature_region_addition -q
```

Expected: isolated seal artifact test fails; real seal addition remains.

- [ ] **Step 4: Implement seal artifact suppression**

In `_suppression_reason`, add before `_has_critical_field_change(diff)`:

```python
        if self._looks_like_isolated_seal_artifact_text(diff):
            return "isolated_seal_artifact_text"
```

Add:

```python
    def _looks_like_isolated_seal_artifact_text(self, diff: DiffItem) -> bool:
        if diff.source_type != "seal":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if "SEAL_OR_SIGNATURE_RISK" not in flags:
            return False
        changed = self._compact(self._changed_text(diff))
        if not changed or len(changed) > 2:
            return False
        text = self._changed_text(diff)
        if self.business_token_pattern.search(text) or self._canonical_date(text):
            return False
        return True
```

- [ ] **Step 5: Run targeted tests and commit**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_suppresses_isolated_seal_region_ocr_fragment backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_real_seal_or_signature_region_addition -q
```

Expected: both pass.

Commit:

```bash
git add backend/app/services/diff_quality.py backend/tests/test_text_cleaning_quality.py
git commit -m "fix(diff): suppress isolated seal OCR artifacts"
```

## Task 5: Protection Regression Suite

**Files:**
- Test: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Add one grouped protection test for real differences**

Append:

```python
def test_diff_quality_keeps_e9_real_visual_differences() -> None:
    diffs = [
        DiffItem(
            diff_id="D050",
            diff_type="MODIFY",
            source_type="clause",
            original_text="按以下第一种方式处理:",
            compare_text="按以下第二种方式处理:",
            original_snippet="一",
            compare_snippet="二",
            review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        ),
        DiffItem(
            diff_id="D032",
            diff_type="MODIFY",
            source_type="clause",
            original_text="人民币(大写)柒万捌仟元整(¥73000.00元)",
            compare_text="人民币(大写)柒万叁仟元整(¥73000.00元)",
            original_snippet="捌仟",
            compare_snippet="叁仟",
            review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        ),
        DiffItem(
            diff_id="D082",
            diff_type="MODIFY",
            source_type="clause",
            original_text="电监安全(2006)34号",
            compare_text="电监安全〔2006〕34号",
            original_snippet="(2006)34",
            compare_snippet="〔2006〕34",
            review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        ),
        DiffItem(
            diff_id="D073",
            diff_type="MODIFY",
            source_type="clause",
            original_text="按以下第一种方式处理:",
            compare_text="按以下第二种方式处理:",
            original_snippet="一/",
            compare_snippet="二",
            review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert [item.diff_id for item in result.diffs] == ["D050", "D032", "D082", "D073"]
```

- [ ] **Step 2: Run the protection test**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py::test_diff_quality_keeps_e9_real_visual_differences -q
```

Expected: pass.

- [ ] **Step 3: Run all text quality tests**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit protection tests if not already included**

If Task 5 added new uncommitted tests, commit:

```bash
git add backend/tests/test_text_cleaning_quality.py
git commit -m "test(diff): protect real visual differences"
```

## Task 6: Task-Level Spot Check And Full Verification

**Files:**
- No required file changes.

- [ ] **Step 1: Run related tests**

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py -q
uv run pytest backend/tests/test_table_compare_structured.py backend/tests/test_matcher_optimization.py -q
```

Expected: both commands pass.

- [ ] **Step 2: Run lint and syntax checks**

Run:

```bash
uv run ruff check backend/app/services/diff_quality.py backend/app/services/diff/boundary_coverage.py backend/tests/test_text_cleaning_quality.py
uv run python -m compileall backend/app backend/tests
```

Expected: ruff reports `All checks passed!`; compileall exits 0.

- [ ] **Step 3: Run the full backend test suite**

Run:

```bash
uv run pytest backend/tests -q
```

Expected: all tests pass. Warnings from existing OCR/PDF dependencies are acceptable if tests pass.

- [ ] **Step 4: Run task-level spot check**

Run a read-only spot check using the existing task data:

```bash
PYTHONPATH=backend python - <<'PY'
import json
from pathlib import Path
from app.models import BBox, Clause, DiffItem, Document, Page, TextBlock
from app.services.diff_quality import DiffQualityProcessor

root = Path("storage/tasks/e9debd86-ada8-45d6-a810-bf1185033fc7")

def load_json(rel: str):
    return json.loads((root / rel).read_text())

def build_doc(rel: str, filename: str) -> Document:
    data = load_json(rel)
    if isinstance(data, dict) and "document" in data:
        pages = []
        for raw_page in data["document"]["pages"]:
            blocks = []
            for index, block in enumerate(raw_page.get("blocks") or []):
                box = block.get("bbox") or {"x0": 0, "y0": 0, "x1": 0, "y1": 0}
                blocks.append(
                    TextBlock(
                        block_id=f"p{raw_page['page_no']}_b{index}",
                        page_no=raw_page["page_no"],
                        text=block.get("text") or "",
                        bbox=BBox(**box),
                    )
                )
            pages.append(Page(page_no=raw_page["page_no"], width=600, height=850, blocks=blocks))
        return Document(filename=filename, path=str(root / rel), page_count=len(pages), pages=pages)

    pages = []
    for item in data:
        page_no = int(item.get("page_index", 0)) + 1
        blocks = []
        for index, text in enumerate(item.get("rec_texts") or []):
            box = (item.get("rec_boxes") or [[0, 0, 0, 0]])[index] if index < len(item.get("rec_boxes") or []) else [0, 0, 0, 0]
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}_b{index}",
                    page_no=page_no,
                    text=text,
                    bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
                )
            )
        pages.append(Page(page_no=page_no, width=600, height=850, blocks=blocks))
    return Document(filename=filename, path=str(root / rel), page_count=len(pages), pages=pages)

def build_clauses(rel: str) -> list[Clause]:
    clauses = []
    for item in load_json(rel):
        text = item.get("text") or item.get("text_preview") or ""
        clauses.append(
            Clause(
                clause_id=item["clause_id"],
                clause_no=item.get("clause_no") or "",
                title=item.get("title") or "",
                text=text,
                normalized_text=text,
                match_text=text,
                page_numbers=item.get("page_numbers") or [],
                section_type=item.get("section_type") or "main_contract",
                section_path=item.get("section_path") or [],
                clause_key=item.get("clause_key") or "",
                order_index=item.get("order_index") or 0,
                split_flags=item.get("split_flags") or [],
            )
        )
    return clauses

task = load_json("task.json")
diffs = [DiffItem.model_validate(item) for item in task["diffs"]]
result = DiffQualityProcessor().process(
    diffs,
    original_clauses=build_clauses("debug/clauses_original.json"),
    compare_clauses=build_clauses("debug/clauses_compare.json"),
    original_document=build_doc(
        "ocr/original_国能长源随州发电有限公司随县分公司2026年新能源场站功率预测系统授权服务合同_定版__ppocrv5_raw.json",
        "original",
    ),
    compare_document=build_doc("ocr/compare_国能长源随州扫描件_ppocrv5_raw.json", "compare"),
)
kept = {item.diff_id for item in result.diffs}
expected_suppressed = {"D011", "D093", "D094", "D113", "D036", "D053", "D098", "D020", "D091", "D027", "D023"}
expected_kept = {"D050", "D032", "D073", "D082", "D018", "D024", "D025", "D026", "D028", "D029", "D090"}
print("kept_count", len(kept))
print("unexpected_kept_false_positives", sorted(expected_suppressed & kept))
print("unexpected_suppressed_real_diffs", sorted(expected_kept - kept))
if expected_suppressed & kept or expected_kept - kept:
    raise SystemExit(1)
PY
```

Expected:

```text
unexpected_kept_false_positives []
unexpected_suppressed_real_diffs []
```

- [ ] **Step 5: Record final verification in the implementation summary**

Do not commit generated task data, rendered page images, or temporary scripts. Include the command results in the final handoff.
