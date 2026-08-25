# Footer Highlight Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove nested OCR and visual footer highlight boxes that represent the same handwritten annotation without suppressing OCR evidence when visual detection covers only a small fragment.

**Architecture:** Keep visual evidence boxes unchanged for accurate rendering. During OCR deduplication, group visual boxes only within the same visual diff and page, compare the resulting envelope with each OCR box, and suppress the OCR evidence when area coverage is at least 60% or both horizontal coverage is at least 60% and vertical coverage is at least 50%. Run the same evidence-based deduplication again after clause evidence localization so footer OCR misclassified as a clause cannot reintroduce nested boxes.

**Tech Stack:** Python 3, Pydantic, pytest.

## Global Constraints

- Keep `/api/compare/*` response compatibility.
- Do not change frontend rendering behavior.
- Preserve OCR evidence when visual evidence covers only a small fragment.
- Do not combine evidence from different visual diff items.

---

### Task 1: Reproduce nested footer highlights

**Files:**
- Modify: `backend/tests/test_footer_annotation_visual.py`

**Interfaces:**
- Consumes: `FooterAnnotationVisualComparator.remove_overlapping_ocr_diffs(...)`.
- Produces: regression tests for projected coverage, grouped visual evidence, and cross-diff isolation.

- [ ] Add a failing test where one tight visual box covers most OCR width and height but less than 60% of OCR area.
- [ ] Add a failing test where two visual boxes from one diff collectively cover the OCR box but neither does alone.
- [ ] Add a passing protection test proving boxes from separate visual diffs are not grouped.
- [ ] Add a failing test for footer OCR misclassified as clause evidence after evidence localization.
- [ ] Run the focused tests and confirm the two nested-highlight cases fail for the intended reason.

### Task 2: Implement per-diff visual grouping

**Files:**
- Modify: `backend/app/services/footer_annotation_visual.py`

**Interfaces:**
- Consumes: visual `DiffItem` evidence lists.
- Produces: page-specific envelope `EvidenceBox` values used only for OCR deduplication.

- [ ] Group evidence by visual diff and page while preserving original visual evidence for display.
- [ ] Compare OCR coverage by area and by horizontal/vertical projections.
- [ ] Keep the existing small-fragment protection.
- [ ] Apply the same deduplication after all clause evidence has been located.
- [ ] Run all footer annotation tests.

### Task 3: Validate the stored task and backend

**Files:**
- Validate: `storage/tasks/f83f53d9-3c74-4256-a9c2-6c4624d5cd58`

**Interfaces:**
- Consumes: stored OCR and visual footer diffs.
- Produces: no nested left-footer OCR/visual pairs on printed pages 14, 16, 20, 24, 25, 27, 41, 43, 47, and 49.

- [ ] Run deduplication against the stored task evidence and assert the listed OCR diff IDs are suppressed.
- [ ] Rerun the stored task and inspect the final task data.
- [ ] Run `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q`.
- [ ] Run `PYTHONPATH=backend .venv/bin/python -m ruff check backend`.
- [ ] Run `git diff --check`.
