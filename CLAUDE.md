# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Contract comparison MVP (合同差异审查系统) — uploads two PDF contracts, extracts structured text with coordinates via GLM-OCR, splits into clauses, matches and diffs them, then produces highlighted PDFs, screenshots, and a PDF report. Backend is Python/FastAPI, frontend is React/TypeScript with Vite.

## Commands

### Backend

```bash
# Install dependencies
cd backend
python -m pip install -r requirements.txt

# Run dev server (with auto-reload)
python -m uvicorn app.main:app --reload --port 8000
# Or via entry point (for PyCharm run configs)
python app/main.py

# Run all tests
python -m pytest

# Run a single test file
python -m pytest tests/test_api.py

# Run a specific test
python -m pytest tests/test_api.py::test_api_compare_contracts
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # Dev server at http://127.0.0.1:5173
npm test           # vitest run
npm run build      # Production build
```

### Full test suite

```bash
cd backend && python -m pytest
cd ../frontend && npm test && npm run build
```

## Architecture

### Backend Pipeline (`backend/app/services/`)

The comparison runs as a synchronous pipeline orchestrated by `CompareService`:

1. **Extract** — `extractors/` pluggable system (Strategy + Factory pattern). `GLMOCRExtractor` calls the GLM layout-parsing API; `PyMuPDFExtractor` is used internally as a test fallback (not wired into the factory for production). Returns `Document` with per-page `TextBlock`s and `BBox` coordinates.

2. **Split** — `ClauseSplitter` uses regex to detect Chinese/numeric clause markers (第X条, 1.1, etc.), groups text into `Clause` objects with evidence boxes. `TextNormalizer` strips page numbers, contract IDs, and normalizes whitespace/punctuation for matching.

3. **Match** — `ClauseMatcher` does two-pass matching: first by exact clause number, then by fuzzy title/body similarity using RapidFuzz (`token_set_ratio`). Threshold configurable via `MATCH_THRESHOLD` env var (default 85).

4. **Diff** — `DiffEngine` produces `DiffItem`s (ADD/DELETE/MODIFY) from `ClausePair`s. For MODIFY diffs, uses `difflib.SequenceMatcher` at sentence level (split on Chinese punctuation) to extract changed snippets.

5. **Locate** — `EvidenceLocator` binds coordinate evidence to diffs (exact text match → block fallback → clause fallback).

6. **Output** — `PdfHighlighter` (PyMuPDF), `ScreenshotService` (PyMuPDF pixmap rendering), `ReportGenerator` (ReportLab PDF with Chinese font auto-detection).

### Key Data Flow

```
PDF → ExtractionResult → Document → Clause[] → ClausePair[] → DiffItem[] → artifacts
```

### Data Models (`backend/app/models.py`)

- `Document` / `Page` / `TextBlock` / `BBox` — structured PDF content with coordinates
- `Clause` — split clause with normalized text and evidence boxes
- `ClausePair` — matched pair with score and method
- `DiffItem` — identified difference with evidence, screenshots, and AI analysis
- `CompareTask` — full task state persisted through `TaskRepository` as local JSON or PostgreSQL payload

### Storage

Artifacts are stored locally under `storage/` (configurable via `STORAGE_DIR`), while task metadata is stored by `TaskRepository`:
- `uploads/` — uploaded PDFs
- `tasks/` — task JSON files when `TASK_REPOSITORY_BACKEND=local_json`
- `highlighted/` — highlighted PDFs
- `screenshots/` — diff screenshots
- `reports/` — generated PDF reports
- `ocr/` — raw OCR results for debugging

PostgreSQL task metadata is enabled with `TASK_REPOSITORY_BACKEND=postgres` and `DATABASE_URL`, then migrated with `cd backend && alembic upgrade head`.

### Frontend (`frontend/src/`)

React 19 + TypeScript + Vite. Pages: `LoginPage` (hardcoded admin/123456), `UploadPage`, `ResultPage`. API client in `lib/api.ts` with `VITE_API_BASE_URL` env var. Uses `pdfjs-dist` for PDF rendering.

## Testing

Tests use `pytest` with `fake_glm_extractor` fixture (conftest.py) that monkeypatches `CompareService` to use `PyMuPDFExtractor` instead of making real GLM-OCR API calls. Tests create PDFs with `reportlab.canvas` and redirect `settings.storage_dir` to `tmp_path`.

Test files:
- `test_api.py` — full API integration via `TestClient`
- `test_compare_integration.py` — `CompareService` end-to-end
- `test_text_processing.py` — unit tests for normalizer, splitter, matcher
- `test_extractors.py` — extractor factory and GLM payload parsing

## Environment Variables

Key env vars (set in `.env`):
- `GLM_API_KEY` — required for GLM-OCR extraction
- `DOCUMENT_EXTRACTOR` — defaults to `glm_ocr`
- `MATCH_THRESHOLD` — clause matching similarity threshold (default 85)
- `REPORT_FONT_PATH` — Chinese font path for PDF reports (auto-detects system fonts if empty)
- `MAX_UPLOAD_SIZE_MB` — upload size limit (default 30)
- `FRONTEND_CORS_ORIGINS` — comma-separated allowed origins
- `STORAGE_DIR` — base storage directory (default `./storage`)
- `TASK_REPOSITORY_BACKEND` — `local_json` by default, or `postgres`
- `DATABASE_URL` — SQLAlchemy URL used by the PostgreSQL task repository

## Important Notes

- This is a Chinese-language application — UI text, API error messages, and report content are in Chinese
- The clause detection regex (`ClauseSplitter.clause_start_pattern`) handles Chinese numbering patterns (第一条, 一、, （一）) and Arabic numbering (1, 1.1, 1.1.1)
- `GLMOCRExtractor.payload_to_document` is defensive: it handles multiple GLM-OCR response formats (pages array, layout_details, md_results fallback) and normalizes bboxes (handles percentage-based coordinates)
- Task IDs format: `T{12-hex-chars}` (e.g., `T1A2B3C4D5E6`), diff IDs: `D{3-digit-index}` (e.g., `D001`)
