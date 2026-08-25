# Multiple Footer Annotations Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve every substantive lower-left handwritten annotation region on a page without reintroducing isolated speck or scan-artifact false positives.

**Architecture:** Keep the existing registered visual comparison and candidate filters. Group horizontally related glyph components into annotation regions, retain the strongest region plus every additional substantive region, and emit those regions as multiple evidence boxes on the existing page-specific visual diff.

**Tech Stack:** Python 3, OpenCV, NumPy, Pydantic, pytest.

## Global Constraints

- Keep `/api/compare/*` response compatibility.
- Keep one visual `DiffItem` per page and side; represent multiple regions with multiple evidence boxes.
- Preserve color-artifact, page-edge, scan-line, and isolated-speck filtering.
- Preserve OCR/visual overlap removal and page-specific quality processing.

---

### Task 1: Reproduce and fix multiple footer annotation regions

**Files:**
- Modify: `backend/app/services/footer_annotation_visual.py:263-499`
- Test: `backend/tests/test_footer_annotation_visual.py`

**Interfaces:**
- Consumes: `_one_sided_components(source, aligned_opposite) -> list[_InkComponent]`
- Produces: one page-specific `DiffItem` whose evidence list contains every retained annotation region.

- [ ] **Step 1: Write the failing regression test**

Add a synthetic page containing one strong far-left annotation, two aligned glyph components forming a second annotation region, and verify that the resulting diff contains two evidence boxes. Strengthen the existing speck test to require only one evidence box.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_footer_annotation_visual.py -q -k 'multiple_substantive or isolated_speck'`

Expected: the multiple-region test fails because the current implementation keeps only `max(...)`.

- [ ] **Step 3: Implement component grouping and retention**

Add focused helpers that merge horizontally adjacent, baseline-aligned components; retain the strongest region and additional regions with at least 30 novel pixels; and build one evidence box per retained region.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_footer_annotation_visual.py -q`

Expected: all footer visual tests pass.

### Task 2: Validate the real task and regression suite

**Files:**
- Validate: `storage/tasks/f83f53d9-3c74-4256-a9c2-6c4624d5cd58`

**Interfaces:**
- Consumes: the printed-page-10 scan and registered visual comparator.
- Produces: separate highlight evidence for the far-left mark and central-left handwriting.

- [ ] **Step 1: Run the comparator against PDF page 11**

Verify that the page-specific visual diff includes both the far-left and central-left evidence boxes.

- [ ] **Step 2: Run backend verification**

Run: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q`

Run: `PYTHONPATH=backend .venv/bin/python -m ruff check backend`

Run: `git diff --check`

- [ ] **Step 3: Rerun the task and inspect final evidence**

Rerun task `f83f53d9-3c74-4256-a9c2-6c4624d5cd58`, then confirm its completed result contains both lower-left evidence regions on PDF page 11 and no task errors.
