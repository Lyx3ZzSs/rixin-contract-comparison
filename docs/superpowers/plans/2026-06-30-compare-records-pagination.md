# Compare Records Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pagination and created-time filtering to the comparison records page.

**Architecture:** The backend keeps the canonical filtering and pagination logic on `GET /api/compare/records`, using created-time descending order for compare records. The frontend sends page, page size, start date, and end date query params, then renders the current page with compact filters and pagination controls.

**Tech Stack:** FastAPI, Pydantic, pytest, React, TypeScript, Vitest, Testing Library.

---

### Task 1: Backend Records API

**Files:**
- Modify: `backend/app/api.py`
- Modify: `backend/app/api_schemas.py`
- Modify: `backend/app/api_presenters.py`
- Test: `backend/tests/test_api.py`

- [ ] Add a failing API test that creates three compare tasks with different `created_at` values, calls `/api/compare/records?page=1&page_size=1&start_date=2026-05-21&end_date=2026-05-22`, and asserts the response has one matching record plus `total`, `page`, `page_size`, and `total_pages`.
- [ ] Run `python -m pytest backend/tests/test_api.py::test_compare_records_list_supports_pagination_and_created_time_filter`.
- [ ] Add query parameters to `list_records()`, validate `page >= 1` and `1 <= page_size <= 100`, filter by inclusive created date range, then slice.
- [ ] Extend `CompareRecordListResponse` and presenter to include pagination metadata.
- [ ] Re-run the targeted backend API test.

### Task 2: Frontend API Contract

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/lib/api.test.ts`

- [ ] Add types for `CompareRecordListResponse` and `CompareRecordQuery`.
- [ ] Update `getCompareRecords()` to accept optional query params and return the full paginated payload.
- [ ] Add a failing API test that verifies query params are encoded.
- [ ] Run `cd frontend && npm test -- src/lib/api.test.ts`.
- [ ] Implement query serialization and make the test pass.

### Task 3: Records Page UI

**Files:**
- Modify: `frontend/src/pages/ComparisonRecordsPage.tsx`
- Modify: `frontend/src/pages/ComparisonRecordsPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] Add failing UI tests for date filtering and next-page navigation.
- [ ] Run `cd frontend && npm test -- src/pages/ComparisonRecordsPage.test.tsx`.
- [ ] Add controlled date inputs, query/reset buttons, and previous/next pagination.
- [ ] Keep SSE refresh scoped to the current query and current page.
- [ ] Style controls inside the existing records panel with compact, utilitarian controls.
- [ ] Re-run the records page tests.

### Task 4: Verification

**Files:**
- No additional files.

- [ ] Run `python -m pytest backend/tests/test_api.py::test_compare_records_list_uses_compare_tasks_only backend/tests/test_api.py::test_compare_records_list_supports_pagination_and_created_time_filter`.
- [ ] Run `cd frontend && npm test -- src/lib/api.test.ts src/pages/ComparisonRecordsPage.test.tsx`.
- [ ] Run `cd frontend && npm run build`.
