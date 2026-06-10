# Architecture Notes

## Current Shape

The system is a FastAPI backend plus a Vite/React frontend. Backend code lives under `backend/`, while the frontend lives under `frontend/`. Backend routes expose contract comparison and field extraction. Runtime state is persisted in a MinerU-style local file layout under `storage/tasks/{task_id}/`.

## Backend Boundaries

- `api`: HTTP adapters. Keep route handlers thin; they should translate requests, call application services, and map exceptions to HTTP responses.
- `api_schemas`: public request and response contracts. These schemas are stable API DTOs and should not be replaced with domain models or persistence payloads.
- `application`: use-case orchestration. This layer owns task creation and background submission.
- `infrastructure`: adapters for task persistence, task queue metadata, artifact storage, external HTTP clients, and task execution.
- `services`: domain and document-processing services. They should not depend on FastAPI request objects.

## API Contract Boundary

HTTP responses are built through presenter functions and API schemas. Public JSON responses should expose artifact URLs, not local filesystem paths, and should not leak persistence-only fields such as `schema_version`, `revision`, raw result paths, or converted file paths. Internal models such as `CompareTask`, `DiffItem`, and `ExtractionTask` remain service and repository payload models; API handlers should not return `model_dump()` or `to_jsonable()` for whole internal tasks.

## Migration Direction

- Keep public API paths compatible while keeping storage and execution behind interfaces.
- Persist task metadata, execution metadata, and artifacts in the task directory.
- Keep uploaded PDFs, OCR raw output, debug files, and reports behind the `ArtifactStore` interface.
- Keep task execution behind `QueuedTaskRunner`; production can replace the local JSON job repository with a broker-backed adapter without changing API or service code.
- Split large comparison modules incrementally. Table comparison now keeps the public `TableComparator` entrypoint while moving constants and internal row/cell/diff support types into separate modules; future algorithm changes should continue that pattern by moving cohesive logic behind narrow helpers.

## Artifact And Client Boundaries

Runtime file locations are resolved through `ArtifactStore`. Uploads, OCR/LLM raw JSON, compare debug files, and reports should not be built by direct `settings.*_dir` path concatenation outside infrastructure adapters. The default implementation is local filesystem storage under `storage/tasks/{task_id}/`, with `manifest.json` tracking generated artifacts. Online comparison highlights are rendered by the frontend from diff evidence coordinates; the backend no longer renders or exports highlighted PDFs.

External OCR, PP-Structure, and LLM calls go through `HttpClientProvider`. Extractors and extraction services accept the provider and app settings through constructors, which keeps tests injectable and avoids hard-wiring module-level clients into domain flow.

## Task Execution

Compare and extraction submissions are serialized into queue payloads before execution. The default runner stores job metadata in `storage/tasks/{task_id}/job.json`, enforces a configurable worker limit, records attempts and terminal state, renews leases while jobs are running, and supports retry and queued-job cancellation through the runner boundary.

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

PP-Structure response conversion is centralized in the shared layout adapter.
Production extraction and the model orchestration layer must reuse that adapter
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
TASK_RUNNER_MAX_ATTEMPTS=1
TASK_RUNNER_LEASE_SECONDS=3600
TASK_RUNNER_RETRY_DELAY_SECONDS=2
TASK_RUNNER_POLL_INTERVAL_SECONDS=0.25
```

## Local Storage

The local storage layout is intentionally close to MinerU output conventions: each task owns a directory and all metadata or artifacts are grouped below it.

```text
storage/
  tasks/
    {task_id}/
      task.json
      job.json
      manifest.json
      uploads/
      ocr/
      debug/
      reports/
```

`task.json` contains the complete `CompareTask` or `ExtractionTask` payload. `job.json` contains queue execution metadata. `manifest.json` is an artifact index maintained by the local repository and artifact store. The frontend login remains hard-coded for now; real users, permissions, and tenant-aware task lists should be designed separately if the product needs them later.

## Verification Baseline

Run these checks after architecture changes:

```bash
cd backend
python -m compileall app tests
python -m pytest
cd ..
cd frontend && npm test && npm run build
```
