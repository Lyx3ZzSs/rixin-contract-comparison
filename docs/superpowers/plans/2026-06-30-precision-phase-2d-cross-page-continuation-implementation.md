# Precision Phase 2D Cross-Page Clause Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge conservatively detected cross-page clause continuations before matcher/diff so true small text changes are not amplified into false large `MODIFY`, `ADD`, or `DELETE` diffs.

**Architecture:** Extend the existing `ParagraphBuilder` instead of adding a post-diff repair layer. The builder will detect adjacent-page continuation units using text-boundary, page, bbox, indentation, and marker-safety signals, then reuse the existing merge path so `page_numbers`, `evidences`, `source_block_ids`, and `char_boxes` are preserved.

**Tech Stack:** Python 3.12, existing Pydantic models, `ClauseSplitter`, `ParagraphBuilder`, pytest, ruff.

---

## File Structure

- Modify `backend/app/services/clause_paragraphs.py`
  - Add cross-page continuation constants.
  - Allow numeric value continuation text to bypass the current “any marker starts a new unit” guard.
  - Add adjacent-page visual continuation checks.
  - Add signing/title boundary checks.
  - Add `CROSS_PAGE_CONTINUATION_MERGED` to merged unit flags when merged units span pages.
- Modify `backend/tests/test_text_cleaning_quality.py`
  - Add end-to-end `ClauseSplitter` tests for cross-page merge, new-clause boundary, title boundary, amount continuation, signing boundary, and evidence retention.
- No API, frontend, matcher, diff builder, table compare, or model-routing files should be changed in this phase.

## Constants

Use this exact split flag:

```python
CROSS_PAGE_CONTINUATION_MERGED = "CROSS_PAGE_CONTINUATION_MERGED"
```

Keep existing:

```python
PARAGRAPH_MERGED = "PARAGRAPH_MERGED"
```

Do not add `PARAGRAPH_CONTINUATION_MERGED` in this implementation. The accepted design marked it optional, and the current `PARAGRAPH_MERGED` flag already covers same-page paragraph merging.

## Task 1: Add Focused End-To-End Tests

**Files:**
- Modify: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Append failing tests near existing clause splitter paragraph tests**

Add these tests after `test_clause_splitter_merges_decimal_amount_continuation_into_previous_clause()`:

```python
def test_clause_splitter_merges_cross_page_clause_continuation_with_evidence() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1-heading",
                        page_no=1,
                        text="5.2 服务期限",
                        bbox=BBox(x0=50, y0=720, x1=500, y1=748),
                    ),
                    TextBlock(
                        block_id="p1-body",
                        page_no=1,
                        text="乙方应在收到甲方书面通知后提供连续运维服务",
                        bbox=BBox(x0=70, y0=760, x1=520, y1=790),
                    ),
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2-body",
                        page_no=2,
                        text="并按照本合同约定提交服务报告。",
                        bbox=BBox(x0=70, y0=72, x1=520, y1=102),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["5.2"]
    assert "连续运维服务" in clauses[0].text
    assert "提交服务报告" in clauses[0].text
    assert clauses[0].page_numbers == [1, 2]
    assert clauses[0].source_block_ids == ["p1-heading", "p1-body", "p2-body"]
    assert "PARAGRAPH_MERGED" in clauses[0].split_flags
    assert "CROSS_PAGE_CONTINUATION_MERGED" in clauses[0].split_flags
    assert [box.page_no for box in clauses[0].bboxes] == [1, 1, 2]
```

- [ ] **Step 2: Add boundary tests in the same section**

Add these tests after the previous test:

```python
def test_clause_splitter_does_not_merge_cross_page_explicit_new_clause() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.2 乙方应持续提供服务",
                        bbox=BBox(x0=50, y0=760, x1=520, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2",
                        page_no=2,
                        text="第六条 违约责任",
                        bbox=BBox(x0=50, y0=72, x1=520, y1=102),
                    )
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["5.2", "第六条"]
    assert "违约责任" not in clauses[0].text
    assert "CROSS_PAGE_CONTINUATION_MERGED" not in clauses[0].split_flags


def test_clause_splitter_does_not_merge_cross_page_standalone_title() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.2 乙方应持续提供服务",
                        bbox=BBox(x0=50, y0=760, x1=520, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2-title",
                        page_no=2,
                        text="违约责任",
                        bbox=BBox(x0=50, y0=72, x1=180, y1=102),
                    ),
                    TextBlock(
                        block_id="p2-body",
                        page_no=2,
                        text="任何一方违约均应承担赔偿责任。",
                        bbox=BBox(x0=70, y0=116, x1=520, y1=146),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 2
    assert "违约责任" not in clauses[0].text
    assert "CROSS_PAGE_CONTINUATION_MERGED" not in clauses[0].split_flags
```

- [ ] **Step 3: Add amount continuation and signing boundary tests**

Add these tests after the previous boundary tests:

```python
def test_clause_splitter_merges_cross_page_amount_value_continuation() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.3 合同总价为人民币",
                        bbox=BBox(x0=50, y0=760, x1=520, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2",
                        page_no=2,
                        text="100000元整，包含税费及安装调试费用。",
                        bbox=BBox(x0=70, y0=72, x1=520, y1=102),
                    )
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["5.3"]
    assert "100000元整" in clauses[0].text
    assert clauses[0].page_numbers == [1, 2]
    assert "CROSS_PAGE_CONTINUATION_MERGED" in clauses[0].split_flags


def test_clause_splitter_does_not_merge_cross_page_no正文_signing_boundary() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.4 本合同附件与正文具有同等法律效力",
                        bbox=BBox(x0=50, y0=760, x1=520, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="sign-none",
                        page_no=2,
                        text="以下无正文",
                        bbox=BBox(x0=50, y0=72, x1=220, y1=102),
                    ),
                    TextBlock(
                        block_id="sign-party",
                        page_no=2,
                        text="甲方（盖章）：",
                        bbox=BBox(x0=50, y0=130, x1=220, y1=160),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) >= 2
    assert "以下无正文" not in clauses[0].text
    assert "CROSS_PAGE_CONTINUATION_MERGED" not in clauses[0].split_flags
```

- [ ] **Step 4: Run tests to verify the new coverage fails**

Run:

```bash
cd backend
python -m pytest \
  tests/test_text_cleaning_quality.py::test_clause_splitter_merges_cross_page_clause_continuation_with_evidence \
  tests/test_text_cleaning_quality.py::test_clause_splitter_does_not_merge_cross_page_explicit_new_clause \
  tests/test_text_cleaning_quality.py::test_clause_splitter_does_not_merge_cross_page_standalone_title \
  tests/test_text_cleaning_quality.py::test_clause_splitter_merges_cross_page_amount_value_continuation \
  tests/test_text_cleaning_quality.py::test_clause_splitter_does_not_merge_cross_page_no正文_signing_boundary \
  -v
```

Expected:

- At least `test_clause_splitter_merges_cross_page_clause_continuation_with_evidence` fails because `p2-body` is not merged or `CROSS_PAGE_CONTINUATION_MERGED` is missing.
- `test_clause_splitter_merges_cross_page_amount_value_continuation` should fail if the amount continuation is not merged or the new flag is missing.

- [ ] **Step 5: Keep the failing tests uncommitted**

Do not commit the red tests. Leave `backend/tests/test_text_cleaning_quality.py` modified in the worktree so Task 2 can make the tests pass and commit tests plus implementation together.

## Task 2: Implement Cross-Page Continuation Detection

**Files:**
- Modify: `backend/app/services/clause_paragraphs.py`

- [ ] **Step 1: Add constants and helper predicates**

In `ParagraphBuilder`, add these class attributes below `continuation_punctuation`:

```python
    paragraph_merged_flag = "PARAGRAPH_MERGED"
    cross_page_merged_flag = "CROSS_PAGE_CONTINUATION_MERGED"
    title_block_types = {"paragraph_title", "doc_title", "title"}
    boundary_block_types = {
        "footer",
        "header",
        "page_footer",
        "page_header",
        "footnote",
        "vision_footnote",
        "table",
        "table_title",
        "seal",
        "image",
        "figure",
    }
```

Add these methods after `_marker_title()`:

```python
    @classmethod
    def _has_strong_boundary_block_type(cls, unit: Any) -> bool:
        block_type = str(getattr(unit, "block_type", "") or "").lower()
        return block_type in cls.boundary_block_types or block_type in cls.title_block_types

    @staticmethod
    def _compact_text(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    @classmethod
    def _looks_like_signing_boundary(cls, text: str) -> bool:
        compact = cls._compact_text(text)
        if not compact:
            return True
        if "以下无正文" in compact:
            return True
        if compact in {"签署页", "签字页"}:
            return True
        if len(compact) <= 20 and re.search(r"(甲方|乙方|买方|卖方).{0,8}(盖章|签章|签字)", compact):
            return True
        if len(compact) <= 20 and re.fullmatch(r"(甲方|乙方|买方|卖方)[:：]?", compact):
            return True
        return False

    @staticmethod
    def _looks_like_numeric_value_continuation(text: str) -> bool:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        return bool(
            re.match(
                r"^\s*\d+(?:,\d{3})*(?:\.\d+)?\s*(?:元整|元|万元|亿元|%|‰|天|日|个月|月|年|台|套|个|项|批|份|件)",
                first_line,
                re.IGNORECASE,
            )
            or re.match(r"^\s*(?:人民币|¥|￥)\s*\d", first_line, re.IGNORECASE)
        )

    @classmethod
    def _current_marker_blocks_continuation(cls, marker: object | None, text: str) -> bool:
        return marker is not None and not cls._looks_like_numeric_value_continuation(text)
```

- [ ] **Step 2: Update `_is_continuation()`**

Replace the current `_is_continuation()` body with:

```python
        if getattr(previous, "section_type", "main_contract") != getattr(current, "section_type", "main_contract"):
            return False

        previous_text = str(getattr(previous, "text", "") or "").strip()
        current_text = str(getattr(current, "text", "") or "").strip()
        if not previous_text or not current_text:
            return False
        if self._has_strong_boundary_block_type(current):
            return False
        if self._looks_like_signing_boundary(current_text):
            return False

        current_marker = parse_marker(current_text)
        if self._current_marker_blocks_continuation(current_marker, current_text):
            return False

        previous_marker = parse_marker(previous_text)
        if previous_marker is not None and not self._marker_title(previous_marker):
            return True

        current_is_standalone_label = (
            self._looks_like_standalone_label(current_text)
            and not self._looks_like_numeric_value_continuation(current_text)
        )
        if previous_text.endswith(self.terminal_punctuation):
            return False
        if self._same_block(previous, current):
            return True
        if getattr(previous, "page_no", None) == getattr(current, "page_no", None):
            if previous_text.endswith(self.continuation_punctuation):
                return True
            return self._visually_close(previous, current) and not current_is_standalone_label
        return self._visually_continues_across_adjacent_pages(previous, current) and not current_is_standalone_label
```

- [ ] **Step 3: Add cross-page visual helper**

Add this method after `_visually_close()`:

```python
    @staticmethod
    def _visually_continues_across_adjacent_pages(previous: Any, current: Any) -> bool:
        previous_page = getattr(previous, "page_no", None)
        current_page = getattr(current, "page_no", None)
        if previous_page is None or current_page is None or current_page != previous_page + 1:
            return False
        previous_bbox = getattr(previous, "bbox", None)
        current_bbox = getattr(current, "bbox", None)
        if previous_bbox is None or current_bbox is None:
            return False

        previous_height = max(1.0, previous_bbox.y1 - previous_bbox.y0)
        current_height = max(1.0, current_bbox.y1 - current_bbox.y0)
        indent_delta = abs(current_bbox.x0 - previous_bbox.x0)
        if indent_delta > max(previous_height * 3.0, current_height * 3.0, 36.0):
            return False

        max_coordinate = max(
            abs(previous_bbox.y0),
            abs(previous_bbox.y1),
            abs(current_bbox.y0),
            abs(current_bbox.y1),
        )
        if max_coordinate <= 2.0:
            previous_near_bottom = previous_bbox.y0 >= 0.55 or previous_bbox.y1 >= 0.68
            current_near_top = current_bbox.y0 <= 0.35
        else:
            previous_near_bottom = previous_bbox.y0 >= 500.0 or previous_bbox.y1 >= 650.0
            current_near_top = current_bbox.y0 <= 180.0
        return previous_near_bottom and current_near_top
```

- [ ] **Step 4: Update `_merge_units()` flags**

In `_merge_units()`, replace the `split_flags = tuple(...)` block with:

```python
        merge_flags = [self.paragraph_merged_flag]
        if getattr(previous, "page_no", None) != getattr(current, "page_no", None):
            merge_flags.append(self.cross_page_merged_flag)
        split_flags = tuple(
            dict.fromkeys([
                *getattr(previous, "split_flags", ()),
                *getattr(current, "split_flags", ()),
                *merge_flags,
            ])
        )
```

In the `except TypeError` branch, replace:

```python
            return self._with_flag(previous, "PARAGRAPH_MERGED")
```

with:

```python
            fallback = self._with_flag(previous, self.paragraph_merged_flag)
            if getattr(previous, "page_no", None) != getattr(current, "page_no", None):
                fallback = self._with_flag(fallback, self.cross_page_merged_flag)
            return fallback
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
cd backend
python -m pytest \
  tests/test_text_cleaning_quality.py::test_clause_splitter_merges_cross_page_clause_continuation_with_evidence \
  tests/test_text_cleaning_quality.py::test_clause_splitter_does_not_merge_cross_page_explicit_new_clause \
  tests/test_text_cleaning_quality.py::test_clause_splitter_does_not_merge_cross_page_standalone_title \
  tests/test_text_cleaning_quality.py::test_clause_splitter_merges_cross_page_amount_value_continuation \
  tests/test_text_cleaning_quality.py::test_clause_splitter_does_not_merge_cross_page_no正文_signing_boundary \
  -v
```

Expected: all five tests pass.

- [ ] **Step 6: Commit implementation and the now-passing tests**

```bash
git add backend/app/services/clause_paragraphs.py backend/tests/test_text_cleaning_quality.py
git commit -m "feat: merge cross-page clause continuations"
```

## Task 3: Regression Coverage For Existing Paragraph Behavior

**Files:**
- Modify: `backend/tests/test_text_cleaning_quality.py`

- [ ] **Step 1: Add a normalized-coordinate regression test**

Add this test after the cross-page evidence test. It verifies the helper works for OCR outputs that store bbox coordinates as normalized `0..1` values:

```python
def test_clause_splitter_merges_normalized_bbox_cross_page_continuation() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=1,
                height=1,
                blocks=[
                    TextBlock(
                        block_id="p1-heading",
                        page_no=1,
                        text="5.5 服务交付",
                        bbox=BBox(x0=0.08, y0=0.88, x1=0.86, y1=0.92),
                    ),
                    TextBlock(
                        block_id="p1-body",
                        page_no=1,
                        text="乙方应完成系统部署并提供",
                        bbox=BBox(x0=0.12, y0=0.94, x1=0.88, y1=0.98),
                    ),
                ],
            ),
            Page(
                page_no=2,
                width=1,
                height=1,
                blocks=[
                    TextBlock(
                        block_id="p2-body",
                        page_no=2,
                        text="不少于三十日的试运行支持。",
                        bbox=BBox(x0=0.12, y0=0.06, x1=0.88, y1=0.10),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["5.5"]
    assert "试运行支持" in clauses[0].text
    assert clauses[0].page_numbers == [1, 2]
    assert "CROSS_PAGE_CONTINUATION_MERGED" in clauses[0].split_flags
```

- [ ] **Step 2: Run paragraph and splitter regression tests**

Run:

```bash
cd backend
python -m pytest tests/test_text_cleaning_quality.py -v
```

Expected: all tests in `test_text_cleaning_quality.py` pass.

- [ ] **Step 3: Run related matcher/diff tests**

Run:

```bash
cd backend
python -m pytest tests/test_clause_alignment.py tests/test_matcher_optimization.py tests/test_diff_match_patch_engine.py -v
```

Expected: all selected tests pass.

- [ ] **Step 4: Commit regression coverage**

```bash
git add backend/tests/test_text_cleaning_quality.py
git commit -m "test: cover normalized cross-page continuation"
```

## Task 4: Final Verification And Documentation Check

**Files:**
- No code changes expected.
- Review: `docs/superpowers/specs/2026-06-30-precision-phase-2d-cross-page-continuation-design.md`

- [ ] **Step 1: Run syntax and lint checks**

Run:

```bash
cd backend
python -m compileall app tests
python -m ruff check .
```

Expected:

- `compileall` exits `0`.
- `ruff` prints `All checks passed!`.

- [ ] **Step 2: Run full backend tests**

Run:

```bash
cd backend
python -m pytest
```

Expected: all backend tests pass.

- [ ] **Step 3: Run quality regression smoke**

Run:

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2d-final \
  --run-id precision-p2d-final \
  --fail-on-regression
```

Expected JSON includes:

```json
{
  "status": "PASSED",
  "failed_gates": []
}
```

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git diff --stat HEAD~3..HEAD
git status --short
```

Expected:

- Phase 2D committed changes touch only:
  - `backend/app/services/clause_paragraphs.py`
  - `backend/tests/test_text_cleaning_quality.py`
- Existing unrelated dirty files may still appear in `git status --short`; do not stage or revert them.

- [ ] **Step 5: Final review**

Use Subagent-Driven final code review over the Phase 2D implementation commits. The reviewer should check:

- Cross-page merge only applies to adjacent pages.
- Explicit new clauses and standalone titles do not merge.
- Signing boundary text does not merge.
- Numeric amount continuation can merge.
- `CROSS_PAGE_CONTINUATION_MERGED` is only added when merged units span pages.
- Evidence/page/source block metadata are preserved.

Expected: reviewer returns `APPROVED`, or any findings are fixed and re-reviewed before completion.
