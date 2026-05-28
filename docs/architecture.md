# Architecture Notes

## Current Shape

The system is a FastAPI backend plus a Vite/React frontend. Backend code lives under `backend/`, while the frontend lives under `frontend/`. Backend routes expose contract comparison and field extraction. Runtime artifacts are stored locally under root `storage/`, while task metadata is persisted through the task repository adapter. The default adapter uses local JSON; production can switch to PostgreSQL with `TASK_REPOSITORY_BACKEND=postgres`.

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
- Keep local JSON and PostgreSQL behind the same `TaskRepository` interface.
- Keep uploaded PDFs, OCR raw output, screenshots, debug files, and reports behind the `ArtifactStore` interface; persist only metadata and artifact paths in the database.
- Keep task execution behind `QueuedTaskRunner`; production can replace the local JSON job repository with a broker-backed adapter without changing API or service code.
- Split large comparison modules incrementally, starting with table comparison normalization, matching, and diff rendering.

## Artifact And Client Boundaries

Runtime file locations are resolved through `ArtifactStore`. Uploads, highlighted PDFs, screenshots, OCR/LLM raw JSON, compare debug files, and reports should not be built by direct `settings.*_dir` path concatenation outside infrastructure adapters. The default implementation is local filesystem storage under `storage/`, but service code receives the store as a dependency so an object-store implementation can be added later.

External OCR, PP-Structure, and LLM calls go through `HttpClientProvider`. Extractors and extraction services accept the provider and app settings through constructors, which keeps tests injectable and avoids hard-wiring module-level clients into domain flow.

## Task Execution

Compare and extraction submissions are serialized into queue payloads before execution. The default runner stores job metadata under `storage/task_jobs`, enforces a configurable worker limit, records attempts and terminal state, renews leases while jobs are running, and supports retry and queued-job cancellation through the runner boundary. Task services still own domain status such as `PROCESSING`, `COMPLETED`, and `FAILED`; queue job status is infrastructure metadata and is not part of the public API DTOs.

Useful settings:

```bash
TASK_RUNNER_MAX_WORKERS=2
TASK_RUNNER_MAX_ATTEMPTS=1
TASK_RUNNER_LEASE_SECONDS=3600
TASK_RUNNER_RETRY_DELAY_SECONDS=2
TASK_RUNNER_POLL_INTERVAL_SECONDS=0.25
```

## Database

PostgreSQL stores task metadata in `task_records`. Frequently queried fields such as `task_type`, `status`, and `updated_at` are indexed columns. Full `CompareTask` and `ExtractionTask` payloads remain in `payload JSONB` during the first database phase to preserve API compatibility and avoid premature table splitting.

Run migrations with:

```bash
cd backend
alembic upgrade head
```

Import existing local JSON tasks with:

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
