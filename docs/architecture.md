# Architecture Notes

## Current Shape

The system is a contract-comparison product with a FastAPI backend and a Vite/React frontend. Backend code lives under `backend/`, while the frontend lives under `frontend/`. Structured runtime state is persisted in `storage/tasks.sqlite3`; task-owned files live under `storage/tasks/{task_id}/`.

## Backend Boundaries

- `api`: HTTP adapters. Keep route handlers thin; they should translate requests, call application services, and map exceptions to HTTP responses.
- `api_schemas`: public request and response contracts. These schemas are stable API DTOs and should not be replaced with domain models or persistence payloads.
- `application`: use-case orchestration. This layer owns task creation and background submission.
- `infrastructure`: adapters for SQLite task persistence, artifact storage, external HTTP clients, and process-local task execution.
- `services`: domain and document-processing services. They should not depend on FastAPI request objects.

## API Contract Boundary

HTTP responses are built through presenter functions and API schemas. Public JSON responses should expose artifact URLs, not local filesystem paths, and should not leak persistence-only fields such as `schema_version`, `revision`, raw result paths, or converted file paths. Internal models such as `CompareTask` and `DiffItem` remain service and repository payload models; API handlers should not return `model_dump()` or `to_jsonable()` for whole internal tasks.

## Architecture Direction

- Keep public API paths compatible while keeping storage and execution behind interfaces.
- Persist task and current-execution metadata in SQLite; persist only binary and diagnostic artifacts in the task directory.
- Keep uploaded PDFs, OCR raw output, debug files, and reports behind the `ArtifactStore` interface.
- Keep task execution behind `QueuedTaskRunner`. The current product intentionally targets one API process and has no durable job queue.
- Split large comparison modules incrementally. Table comparison now keeps the public `TableComparator` entrypoint while moving constants and internal row/cell/diff support types into separate modules; future algorithm changes should continue that pattern by moving cohesive logic behind narrow helpers.

## Artifact And Client Boundaries

Runtime file locations are resolved through `ArtifactStore`. Uploads, OCR raw JSON, compare debug files, and reports should not be built by direct path concatenation outside infrastructure adapters. Files are discovered from the fixed task layout; there is no artifact manifest. Online comparison highlights are rendered by the frontend from diff evidence coordinates; the backend no longer renders or exports highlighted PDFs.

External OCR and PP-Structure calls go through `HttpClientProvider`. Document extractors and document-processing services accept the provider and app settings through constructors, which keeps tests injectable and avoids hard-wiring module-level clients into domain flow.

## Task Execution

Compare submissions are placed on an in-memory queue after the task and uploads are committed. The runner enforces a configurable worker limit and stores only the current execution summary in the task row. It supports manual retry and cancellation, but does not persist queue entries, leases, attempts, or execution history. On startup, every nonterminal task is marked failed with `SERVICE_RESTARTED` so it can be retried explicitly.

Execution metadata is exposed through narrow operational endpoints:

- `GET /api/compare/{task_id}/execution`
- `POST /api/compare/{task_id}/cancel`
- `POST /api/compare/{task_id}/retry`

Task services still own domain status such as `PROCESSING`, `COMPLETED`, and `FAILED`; execution endpoints expose queue job state separately so the main task DTOs stay compatible.

## Pipeline Contracts

The comparison pipeline still runs the same stage order, but stage data should move through typed `PipelineContext` accessors such as `require_extractions`, `set_clauses`, and `set_clause_diffs`. Stages should raise `PipelineContractError` for missing required inputs instead of assuming previous mutable fields are populated.

PP-Structure response conversion is centralized in the shared layout adapter.
Production document extraction and the model orchestration layer must reuse that adapter
instead of implementing separate bbox, label, or table-cell parsing. Hybrid OCR
results expose deterministic `reading_order` values and write layout quality
diagnostics to the task debug directory.
V3 layout analysis adds flow roles and explicit OCR/layout match diagnostics.
Use `v3_shadow` before enabling `v3`; the default remains `v2` until shadow
rollout and approved real-fixture regression thresholds pass.

## Error Boundary

Domain errors inherit from `AppError` and carry their HTTP status mapping at the API boundary. API handlers should prefer `http_error()` over ad hoc `HTTPException` mapping for validation, not-found, conflict, document-processing, and task-execution errors.

Useful settings:

```bash
TASK_RUNNER_MAX_WORKERS=2
```

## Local Storage

SQLite is the sole structured state source. Each task owns a directory containing only files needed to execute the task or serve its current result.

```text
storage/
  tasks.sqlite3
  tasks/
    {task_id}/
      input/
        original/{original_filename}
        compare/{compare_filename}
      report/
        report-r{revision}.pdf
      diagnostics/
        ocr/
        debug/
      staging/
```

Successful tasks delete diagnostics immediately. Failed or low-quality task diagnostics are kept for seven days; stale staging files are removed after 24 hours. Inputs and business task state are retained indefinitely, and reports are generated on demand for the current revision only. Historical JSON task directories are left untouched but are invisible to application reads and lists; users rerun those tasks manually when needed.

The frontend uses `react-oidc-context` with Keycloak as a public OIDC client, following the authorization-code flow with PKCE; OIDC protocol state is stored in browser `sessionStorage`, and the frontend must not contain a Client Secret. The backend validates access tokens and enforces Keycloak roles and task ownership.

## Verification Baseline

Run these checks after architecture changes:

```bash
cd backend
python -m compileall app tests
python -m pytest
cd ..
cd frontend && npm test && npm run build
```
