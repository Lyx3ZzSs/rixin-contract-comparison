# Low-Contrast Footer Annotation Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably highlight pale gray/cyan lower-left handwritten additions without highlighting fingers, scan edges, or deleting useful OCR evidence after a small visual overlap.

**Architecture:** Preserve registered page comparison and one visual `DiffItem` per page. Build candidates from a permissive neutral-ink mask, require either a dark seed or a substantive faint region, remove vertically dense scan-capture bands before connected-component grouping, and deduplicate OCR only when visual evidence covers most of the OCR box.

**Tech Stack:** Python 3, OpenCV, NumPy, Pydantic, pytest.

## Global Constraints

- Keep `/api/compare/*` response compatibility.
- Keep one visual `DiffItem` per page and side with multiple evidence boxes.
- Preserve page registration, printed-content subtraction, and page-specific quality processing.
- Do not weaken the existing colored scan-artifact and isolated-speck protections.

---

### Task 1: Reproduce the three root causes

**Files:**
- Modify: `backend/tests/test_footer_annotation_visual.py`

**Interfaces:**
- Consumes: `FooterAnnotationVisualComparator.build_diffs(...)` and `remove_overlapping_ocr_diffs(...)`.
- Produces: regression coverage for pale neutral ink, dark saturated artifacts, vertically dense capture artifacts, and partial OCR overlap.

- [ ] Add a pale gray/cyan annotation test that fails under the current `source_ink_threshold = 160`.
- [ ] Add a dark saturated artifact test that fails because `gray < 70` currently bypasses saturation filtering.
- [ ] Add a finger/vertical-band test whose adjacent handwriting must survive while the band is excluded.
- [ ] Add a partial-overlap OCR test that fails because the current code removes the entire OCR box.
- [ ] Run the focused tests and confirm each fails for the intended behavior.

### Task 2: Implement the minimal root-cause fixes

**Files:**
- Modify: `backend/app/services/footer_annotation_visual.py`

**Interfaces:**
- Consumes: rendered RGB page images and registered opposite-page images.
- Produces: neutral low-contrast candidate regions with dense vertical artifacts removed, plus coverage-aware OCR deduplication.

- [ ] Raise the permissive source threshold to 240 while retaining a 160 dark-seed mask.
- [ ] Accept a component only when it has enough dark seed pixels or spans at least 20 pixels as a faint annotation.
- [ ] Restrict the footer ROI to `0.915..0.99` page height and remove columns whose ink density is at least 35%, with a minimum threshold of 25 pixels.
- [ ] Extend the left-footer ROI through `0.34` page width so long signatures are not clipped at the previous `0.26` boundary.
- [ ] Merge baseline-aligned signature fragments across gaps up to 1.75 times their maximum character height.
- [ ] Require neutral saturation instead of allowing all very-dark colored pixels.
- [ ] Remove OCR evidence only when visual evidence covers at least 60% of the OCR evidence area.
- [ ] Run the focused footer tests and confirm they pass.

### Task 3: Validate the real task and full backend

**Files:**
- Validate: `storage/tasks/f83f53d9-3c74-4256-a9c2-6c4624d5cd58`

**Interfaces:**
- Consumes: the original and compare PDFs from the stored task.
- Produces: useful left-footer evidence on all 48 signed pages and none on unsigned PDF pages 1, 39, 41, 43, and 45.

- [ ] Run the comparator over all 53 real pages and assert the detected-page set equals the 48 visually signed pages.
- [ ] Recheck printed pages 12, 14, 16, 18, 20, 22, 26, 28, 30, 34, 35, 43, 46, and 48 for substantive evidence boxes.
- [ ] Run `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q`.
- [ ] Run `PYTHONPATH=backend .venv/bin/python -m ruff check backend`.
- [ ] Run `git diff --check`.
