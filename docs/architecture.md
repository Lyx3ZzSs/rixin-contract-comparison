# Architecture Notes

## Current Shape

The system is a FastAPI backend plus a Vite/React frontend. Backend code lives under `backend/`, while the frontend lives under `frontend/`. Backend routes expose contract comparison and field extraction. Runtime artifacts are stored locally under root `storage/`, while task metadata is persisted through the task repository adapter. The default adapter uses local JSON; production can switch to PostgreSQL with `TASK_REPOSITORY_BACKEND=postgres`.

## Backend Boundaries

- `api`: HTTP adapters. Keep route handlers thin; they should translate requests, call application services, and map exceptions to HTTP responses.
- `application`: use-case orchestration. This layer owns task creation and background submission.
- `infrastructure`: replaceable adapters for task persistence, artifact path safety, database access, and task execution.
- `services`: domain and document-processing services. They should not depend on FastAPI request objects.

## Migration Direction

- Keep public API paths compatible while moving storage and execution behind interfaces.
- Keep local JSON and PostgreSQL behind the same `TaskRepository` interface.
- Keep uploaded PDFs, OCR raw output, screenshots, and reports in artifact storage; persist only metadata and artifact paths in the database.
- Replace local executor with a queue worker by implementing the task runner boundary.
- Split large comparison modules incrementally, starting with table comparison normalization, matching, and diff rendering.

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
