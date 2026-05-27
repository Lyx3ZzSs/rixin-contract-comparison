# Architecture Notes

## Current Shape

The system is a FastAPI backend plus a Vite/React frontend. Backend routes expose contract comparison and field extraction. Runtime artifacts are stored locally under `storage/`, while task metadata is persisted as JSON through the task repository adapter.

## Backend Boundaries

- `api`: HTTP adapters. Keep route handlers thin; they should translate requests, call application services, and map exceptions to HTTP responses.
- `application`: use-case orchestration. This layer owns task creation and background submission.
- `infrastructure`: replaceable adapters for task persistence, artifact path safety, and task execution.
- `services`: domain and document-processing services. They should not depend on FastAPI request objects.

## Migration Direction

- Keep public API paths compatible while moving storage and execution behind interfaces.
- Replace local JSON with a database by implementing `TaskRepository`.
- Replace local executor with a queue worker by implementing the task runner boundary.
- Split large comparison modules incrementally, starting with table comparison normalization, matching, and diff rendering.

## Verification Baseline

Run these checks after architecture changes:

```bash
python -m compileall app tests
python -m pytest
cd frontend && npm test && npm run build
```
