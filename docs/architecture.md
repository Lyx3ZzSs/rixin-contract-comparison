# Architecture Notes

## Current Shape

The system is a FastAPI backend plus a Vite/React frontend. Backend code lives under `backend/`, while the frontend lives under `frontend/`. Backend routes expose contract comparison and field extraction. Runtime artifacts are stored locally under root `storage/`, while task metadata and task execution metadata are persisted in PostgreSQL by default.

## Backend Boundaries

- `api`: HTTP adapters. Keep route handlers thin; they should translate requests, call application services, and map exceptions to HTTP responses.
- `api_schemas`: public request and response contracts. These schemas are stable API DTOs and should not be replaced with domain models or persistence payloads.
- `application`: use-case orchestration. This layer owns task creation and background submission.
- `infrastructure`: replaceable adapters for task persistence, task queue metadata, artifact storage, external HTTP clients, database access, and task execution.
- `services`: domain and document-processing services. They should not depend on FastAPI request objects.

## API Contract Boundary

HTTP responses are built through presenter functions and API schemas. Public JSON responses should expose artifact URLs, not local filesystem paths, and should not leak persistence-only fields such as `schema_version`, `revision`, raw result paths, or converted file paths. Internal models such as `CompareTask`, `DiffItem`, and `ExtractionTask` remain service and repository payload models; API handlers should not return `model_dump()` or `to_jsonable()` for whole internal tasks.

## Migration Direction

- Keep public API paths compatible while moving storage and execution behind interfaces.
- Keep PostgreSQL behind repository interfaces for task metadata and task execution metadata. Local JSON adapters remain available only for isolated tests and manual tooling.
- Keep uploaded PDFs, OCR raw output, debug files, highlighted PDFs, and reports behind the `ArtifactStore` interface; persist only metadata and artifact paths in the database.
- Keep task execution behind `QueuedTaskRunner`; production can replace the local JSON job repository with a broker-backed adapter without changing API or service code.
- Split large comparison modules incrementally. Table comparison now keeps the public `TableComparator` entrypoint while moving constants and internal row/cell/diff support types into separate modules; future algorithm changes should continue that pattern by moving cohesive logic behind narrow helpers.

## Artifact And Client Boundaries

Runtime file locations are resolved through `ArtifactStore`. Uploads, highlighted PDFs, OCR/LLM raw JSON, compare debug files, and reports should not be built by direct `settings.*_dir` path concatenation outside infrastructure adapters. The default implementation is local filesystem storage under `storage/`, but service code receives the store as a dependency so an object-store implementation can be added later.

External OCR, PP-Structure, and LLM calls go through `HttpClientProvider`. Extractors and extraction services accept the provider and app settings through constructors, which keeps tests injectable and avoids hard-wiring module-level clients into domain flow.

## Task Execution

Compare and extraction submissions are serialized into queue payloads before execution. The default runner stores job metadata in PostgreSQL, enforces a configurable worker limit, records attempts and terminal state, renews leases while jobs are running, and supports retry and queued-job cancellation through the runner boundary.

Execution metadata is exposed through narrow operational endpoints:

- `GET /api/compare/{task_id}/execution`
- `POST /api/compare/{task_id}/cancel`
- `POST /api/compare/{task_id}/retry`
- `GET /api/extract/{task_id}/execution`
- `POST /api/extract/{task_id}/cancel`
- `POST /api/extract/{task_id}/retry`

Task services still own domain status such as `PROCESSING`, `COMPLETED`, and `FAILED`; execution endpoints expose queue job state separately so the main task DTOs stay compatible.

## Pipeline Contracts

The comparison pipeline still runs the same stage order, but stage data should move through typed `PipelineContext` accessors such as `require_extractions`, `set_clauses`, and `set_clause_diffs`. Stages should raise `PipelineContractError` for missing required inputs instead of assuming previous mutable fields are populated.

## Error Boundary

Domain errors inherit from `AppError` and carry their HTTP status mapping at the API boundary. API handlers should prefer `http_error()` over ad hoc `HTTPException` mapping for validation, not-found, conflict, document-processing, and task-execution errors.

Useful settings:

```bash
TASK_RUNNER_MAX_WORKERS=2
TASK_RUNNER_MAX_ATTEMPTS=1
TASK_RUNNER_LEASE_SECONDS=3600
TASK_RUNNER_RETRY_DELAY_SECONDS=2
TASK_RUNNER_POLL_INTERVAL_SECONDS=0.25
```

## Database

PostgreSQL stores task metadata in `task_records` and execution metadata in `task_jobs`. Frequently queried fields such as `task_type`, `status`, and `updated_at` are indexed columns. Full `CompareTask`, `ExtractionTask`, and task execution payloads remain in `payload JSONB` during the first database phase to preserve API compatibility and avoid premature table splitting.

Runtime requires `DATABASE_URL`; without it, the default repositories fail during startup or first use. Table structure is kept in SQL files under `backend/migrations/sql/`, with Alembic revision wrappers executing the corresponding `up.sql` and `down.sql` files.

Run migrations with:

```bash
cd backend
alembic upgrade head
```

Existing local JSON tasks can still be imported manually when needed:

```bash
cd backend
python scripts/import_tasks_to_db.py --dry-run
python scripts/import_tasks_to_db.py
```

## Verification Baseline

Run these checks after architecture changes:

```bash
cd backend
python -m compileall app tests
python -m pytest
cd ..
cd frontend && npm test && npm run build
```
