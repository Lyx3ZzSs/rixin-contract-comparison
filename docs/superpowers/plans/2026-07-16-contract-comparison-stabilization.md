# Contract Comparison Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the obsolete field-extraction product surface and deliver four sequential, independently stable batches that make comparison task execution, diff/audit semantics, artifact submission, reporting, and single-process deployment deterministic.

**Architecture:** Keep FastAPI adapters thin, move execution state coordination into infrastructure, pass an explicit execution context through the application and pipeline, make AuditItem the only review/report unit, and retain local-file persistence with atomic publication plus a single API-process guard. Each batch is an independently verifiable release checkpoint under the repository's existing one-API-process Docker/default command; Batch 4 turns that interim deployment constraint into an enforced startup invariant. Every checkpoint must pass its targeted tests, the full repository gate, and an independent SubAgent review before the next batch starts.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest, local JSON/file storage, `threading.RLock`, OS advisory locks, ReportLab, React, TypeScript, Vite, Vitest, Testing Library, Docker Compose.

## Global Constraints

- Implement batches strictly in order: Batch 1 → Batch 2 → Batch 3 → Batch 4. Do not start the next batch while the current batch has unresolved blocking review findings.
- Until Batch 4 is complete, Batch 1–3 checkpoints are supported only with one API process and the existing worker-thread model; do not use `uvicorn --workers`, gunicorn pre-fork, or multiple replicas against the same storage. Batch 4 adds pre-start rejection and the OS singleton guard.
- Follow Red-Green-Refactor for every behavior change: add a focused failing test, run it and observe the intended failure, implement the minimum behavior, rerun the focused test, then run the batch regression set.
- Preserve `/api/compare/*` request compatibility. Removing the unused field-extraction product types and `/api/extract/*` documentation is the explicitly approved API migration.
- Preserve document parsing/OCR extraction. `services/extractors/base.py::ExtractionResult`, `extraction_strategy`, `document_extractor`, PPStructure, PPOCRv5, and hybrid OCR settings are not field-extraction product code.
- Do not delete historical extraction files from storage. Compare list/read paths must safely skip them.
- Job terminal states are immutable. A user Retry creates a new Job and is the only approved Task terminal-to-processing path for retryable terminal reasons.
- `AuditItem` is the canonical unit for review, statistics, frontend display, and reports. Diff review fields are compatibility projections only.
- Golden fixtures that encode unrelated correct behavior remain unchanged. A fixture that demonstrably encodes the approved dedupe bug may be changed only with a focused regression test and explicit SubAgent approval of that exact diff.
- Preserve all unrelated working-tree files. Before every commit, inspect `git status --short` and stage only files named by the current task.
- Use repository-relative paths in persisted manifests and structured logs; never log contract text, OCR text, user tokens, or absolute storage paths.

## File and Responsibility Map

| Area | Current files | Target responsibility |
| --- | --- | --- |
| API contracts | `backend/app/api.py`, `api_schemas.py`, `api_presenters.py` | HTTP parsing, stable DTOs, error mapping, no execution state mutation |
| Application orchestration | `backend/app/application/compare_tasks.py` | Create/submit/cancel/retry/review use cases and execution-context handoff |
| Task persistence | `backend/app/infrastructure/task_repository.py` | CompareTask truth, revisioned writes, summary-index updates |
| Job persistence/execution | `backend/app/infrastructure/task_runner.py` | Multi-execution Job history, claims, leases, worker threads |
| Execution coordination | new `backend/app/infrastructure/execution_state.py` | One process-wide lock, immutable Job snapshots, legal transitions, terminal commit |
| Atomic files | new `backend/app/infrastructure/atomic_files.py` | Same-directory temp write, fsync, replace, parent fsync, cleanup |
| Startup recovery | new `backend/app/infrastructure/reconciliation.py` | Idempotent Task/Job crash-window reconciliation |
| Runtime singleton | new `backend/app/infrastructure/runtime_lock.py` | Non-blocking OS advisory lock for one API process |
| Upload/artifacts | `backend/app/utils/file_utils.py`, `backend/app/infrastructure/artifact_store.py` | Streaming staging, validation, scoped compensation, manifest publication |
| Pipeline | `backend/app/services/pipeline.py`, `compare_service.py`, `pipeline_stages.py` | Cancellation checkpoints, pure result return, evidence-aware final dedupe |
| Review/audit | `backend/app/services/audit_summary.py`, `review_service.py` | Canonical AuditItems, migration projection, one review-statistics model |
| Reports | `backend/app/services/report_generator.py`, new `report_store.py` | Complete AuditItem rendering and report-revision concurrency control |
| Progress | `backend/app/services/progress_bus.py` | Capacity-one latest progress with terminal-sticky semantics |
| Quality cases | `backend/app/api_quality.py`, `quality_workbench.py`, new scripts/resources | Production-owned case/run dirs and safe seed initialization |
| Frontend state/API | `frontend/src/types.ts`, `lib/api.ts`, `lib/hooks.ts`, `lib/api_sse.ts` | Terminal-reason projection, normalized AuditItems, abortable HTTP, SSE→polling convergence |
| Frontend views | `frontend/src/pages/ResultPage.tsx`, `ComparisonRecordsPage.tsx` | Independent AuditItem review state and accurate cancelled/failed rendering |
| Deployment | `backend/app/main.py`, `backend/scripts/run_api.py`, `backend/Dockerfile`, `docker-compose.yml`, docs | Pre-uvicorn worker validation, singleton guard, quality seed initialization |

---

## Batch 1 — Product Scope Convergence

### Task 1: Lock the public product surface with failing tests

**Files:**
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/test_task_repository.py`
- Modify: `backend/tests/test_task_runner.py`
- Modify: `backend/tests/test_file_utils.py`
- Modify: `backend/tests/test_pipeline.py`

- [ ] Replace model-based extraction fixtures with raw legacy JSON. The repository compatibility test must write a legacy payload such as:

```python
(settings.tasks_dir / "TEXTRACT").mkdir(parents=True)
(settings.tasks_dir / "TEXTRACT" / "task.json").write_text(
    json.dumps({"task_id": "TEXTRACT", "task_type": "extraction", "status": "COMPLETED"}),
    encoding="utf-8",
)
assert [task.task_id for task in repository.list_compare_tasks()] == ["TCOMPARE"]
```

- [ ] Add `test_openapi_exposes_compare_but_not_extract_routes()` and assert every OpenAPI path is free of `/api/extract` while `/api/compare` remains present.
- [ ] Add `test_api_schema_has_no_field_extraction_product_types()` asserting `api_schemas` no longer exports `ExtractionTaskResponse` or `ExtractionFieldRequest`; this is the deliberately failing removal test.
- [ ] Change the queued-cancel test to register and submit a `compare` handler; remove tests whose only purpose is `ExtractionTask` CRUD or extraction-only upload validation.
- [ ] Add `test_document_extraction_result_and_compare_pipeline_remain_importable()` importing `ExtractionResult`, `DocumentExtractor`, and `ComparePipeline` so cleanup cannot remove document parsing by a broad name match.
- [ ] Run the focused tests and confirm the removal test fails because extraction-product schema symbols still exist:

```bash
cd backend && python -m pytest \
  tests/test_api.py::test_openapi_exposes_compare_but_not_extract_routes \
  tests/test_api.py::test_api_schema_has_no_field_extraction_product_types \
  tests/test_task_repository.py::test_compare_listing_skips_legacy_extraction_payload \
  tests/test_pipeline.py::test_document_extraction_result_and_compare_pipeline_remain_importable
```

Expected: the schema-symbol removal test fails before cleanup. The OpenAPI, legacy compatibility, and import guards are characterization tests that should already pass and must remain green.

### Task 2: Remove backend field-extraction product code

**Files:**
- Delete: `backend/app/models_extraction.py`
- Modify: `backend/app/api_schemas.py`
- Modify: `backend/app/api_presenters.py`
- Modify: `backend/app/infrastructure/task_repository.py`
- Modify: `backend/app/infrastructure/task_runner.py`
- Modify: `backend/app/utils/file_utils.py`
- Modify: `backend/app/utils/json_utils.py`
- Modify: tests from Task 1

- [ ] Narrow execution typing to comparison jobs only:

```python
TaskExecutionType = Literal["compare"]
TaskJobType = Literal["compare"]
```

- [ ] Delete `ExtractionFieldRequest`, `ExtractionFieldResponse`, `ExtractionFieldValueResponse`, `ExtractionTaskResponse`, `ExtractionRecordResponse`, and `ExtractionRecordListResponse`; remove their presenters and imports.
- [ ] Remove `save_extraction_task`, `load_extraction_task`, `list_extraction_tasks`, and `update_extraction_task` from the repository protocol, local implementation, lazy adapter, and JSON utility wrappers.
- [ ] Remove `validate_extraction_upload_bytes`, `save_upload_file_generic`, and extraction-product-only size constants. Keep comparison PDF validation and streaming changes for Batch 3.
- [ ] Delete `models_extraction.py` only after `rg -n "models_extraction|ExtractionTask|ExtractionField" backend/app backend/tests` shows no legitimate use.
- [ ] Rerun the focused tests from Task 1. Expected: all pass.
- [ ] Run backend cleanup checks:

```bash
cd backend && python -m compileall app tests
cd backend && python -m ruff check app tests
```

- [ ] Commit only this backend slice:

```bash
git add backend/app backend/tests
git commit -m "Remove field extraction backend surface"
```

### Task 3: Remove frontend/config/documentation remnants

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/state.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/.env.example`
- Modify: `AGENTS.md`
- Modify: `docs/architecture.md`
- Modify: `docs/deployment.md`
- Modify: `docs/docker-deployment.md`
- Modify: current root `README.md` if matched by the searches below

- [ ] Delete `ExtractionFieldStatus`, `ExtractionFieldValue`, and any unused extraction-product DTOs from `types.ts`; keep `PageProfile.extraction_strategy`.
- [ ] Remove the unused `TOGGLE_EXTRACTION_MENU` state action; there is no route/menu component to delete, so do not claim an entry was removed.
- [ ] Delete the `/* Card-based extraction view */` block, `.extraction-*` record/detail/result rules, and their responsive selectors without changing shared comparison selectors.
- [ ] Remove `VITE_ENABLE_EXTRACTION`, `EXTRACTION_MAX_DOCUMENT_SIZE_MB`, and `EXTRACTION_MAX_IMAGE_SIZE_MB` documentation. Rename ambiguous configuration headings to “Document extraction/OCR” where they refer to parsing.
- [ ] Update current architecture and repository guidance to describe a contract-comparison product and historical extraction-file read tolerance. Historical design/plan documents remain immutable records and are excluded from the zero-remnant assertion.
- [ ] Run these static checks:

```bash
rg -n "class Extraction(Task|Field)|from app\.models_extraction|save_extraction_task|load_extraction_task|list_extraction_tasks|update_extraction_task" backend/app
rg -n "ExtractionField|VITE_ENABLE_EXTRACTION|EXTRACTION_MAX_(DOCUMENT|IMAGE)_SIZE_MB|TOGGLE_EXTRACTION_MENU" \
  frontend/src frontend/.env.example AGENTS.md docs/architecture.md docs/deployment.md docs/docker-deployment.md
rg -n "from app\.models_extraction|save_extraction_task|load_extraction_task|list_extraction_tasks" backend/tests
rg -n "ExtractionResult|extraction_strategy|document_extractor" backend/app frontend/src
```

Expected: the first three commands exit 1 with no product-code/import matches; the last returns legitimate document parsing/OCR matches. Negative OpenAPI tests may intentionally contain the `/api/extract` literal and are not part of the zero-symbol search.

- [ ] Run frontend checks:

```bash
cd frontend && env -u VITE_OIDC_AUTHORITY -u VITE_OIDC_CLIENT_ID npm test
cd frontend && npm run build
```

- [ ] Commit this slice:

```bash
git add AGENTS.md docs/architecture.md docs/deployment.md docs/docker-deployment.md frontend/.env.example frontend/src
git commit -m "Remove field extraction product remnants"
```

### Task 4: Batch 1 verification and SubAgent gate

- [ ] Run the full gate:

```bash
cd backend && python -m compileall app tests
cd backend && python -m ruff check .
cd backend && python -m pytest
cd frontend && env -u VITE_OIDC_AUTHORITY -u VITE_OIDC_CLIENT_ID npm test
cd frontend && npm run build
```

Expected baseline: backend is at least the previously observed 1111 tests minus intentionally deleted extraction-product tests plus new scope tests; frontend is at least the previously observed 129 tests. Record exact counts in the Batch 1 handoff.

- [ ] Spawn one independent SubAgent with this bounded review prompt:

```text
Review Batch 1 against §5.1 and §8.1 of the confirmed stabilization design. Inspect the actual diff and tests. Verify that all field-extraction product models, DTOs, repository methods, job typing, frontend types/flag/CSS, and current docs are gone; historical extraction JSON is safely skipped; ExtractionResult and document OCR extraction remain. Report findings by severity with file:line evidence. Do not edit files.
```

- [ ] Fix every blocking/high finding with a failing regression test first, rerun the full gate, and request the same SubAgent to re-review the fixes.
- [ ] Proceed to Batch 2 only when the SubAgent reports no blocking findings.

---

## Batch 2 — Task, Diff, and Audit Correctness

### Task 5: Define Task/Job state models and legal transitions

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/errors.py`
- Modify: `backend/app/api_schemas.py`
- Modify: `backend/app/api_presenters.py`
- Modify: `backend/app/infrastructure/task_runner.py`
- Test: `backend/tests/test_task_runner.py`
- Test: `backend/tests/test_task_repository.py`
- Test: `backend/tests/test_api.py`

- [ ] Add failing model/contract tests for legacy Task parsing, cancellation projection, immutable Job terminals, and retryable Task reasons.
- [ ] Add the public reason and execution identity fields:

```python
TaskTerminalReason = Literal["NONE", "EXECUTION_FAILED", "SUBMISSION_FAILED", "CANCELLED"]

class CompareTask(BaseModel):
    terminal_reason: TaskTerminalReason = "NONE"
    active_job_id: str = ""
    terminal_job_id: str = ""
    terminal_attempt: int = 0
    report_revision: int = 0

class TaskJob(BaseModel):
    execution_no: int = 1
    error_code: str = ""
```

- [ ] Add a `TaskExecutionError` base plus `TaskCancelled`, `TaskStaleLeaseError`, and `TaskTransitionConflict`. Keep stale-lease handling internal to workers during normal execution, but map both transition conflict and any stale-lease error that reaches an API use case to HTTP 409.
- [ ] Implement explicit transition predicates. Permit only:
  - `PROCESSING -> COMPLETED|FAILED`;
  - `FAILED/EXECUTION_FAILED -> PROCESSING`;
  - `FAILED/SUBMISSION_FAILED -> PROCESSING` when validated inputs still exist.
  Reject `COMPLETED -> *`, `FAILED/CANCELLED -> PROCESSING`, and stale/non-active Job terminal writes.
- [ ] Implement legacy projections: non-FAILED `terminal_reason` → `NONE`; FAILED with cancellation stage/error → `CANCELLED`; other FAILED → `EXECUTION_FAILED`. A completed historical Task missing `report_revision` loads as 1; every other missing value loads as 0.
- [ ] Run focused tests:

```bash
cd backend && python -m pytest tests/test_task_runner.py tests/test_task_repository.py tests/test_api.py -q
```

- [ ] Commit:

```bash
git add backend/app/models.py backend/app/errors.py backend/app/api_schemas.py backend/app/api_presenters.py backend/app/infrastructure/task_runner.py backend/tests
git commit -m "Define comparison task execution states"
```

### Task 6: Persist independent Job executions and retries

**Files:**
- Modify: `backend/app/infrastructure/task_runner.py`
- Modify: `backend/app/application/compare_tasks.py`
- Test: `backend/tests/test_task_runner.py`
- Test: `backend/tests/test_api.py`

- [ ] Add failing tests proving:
  - a newly submitted first execution uses `compare:{task_id}:1` and `jobs/1.json`;
  - user Retry creates `compare:{task_id}:2` in `jobs/2.json` and leaves Job 1 FAILED;
  - only one active Job exists;
  - cancelled/completed Tasks cannot retry;
  - automatic retries reuse the same Job and increment `attempt` on every QUEUED claim and lease takeover;
  - a max-attempt expired lease becomes FAILED with `LEASE_EXPIRED_MAX_ATTEMPTS` without invoking the handler again.
- [ ] Change the Job repository interface to address Jobs by full identity and source path. Every newly created Job uses `jobs/{execution_no}.json`; only a pre-existing historical execution loaded from `job.json` is updated in place.
- [ ] Make `QueuedTaskRunner.retry()` allocate `max(existing.execution_no) + 1`, enqueue a fresh Job with `attempt=0`, and atomically set Task `active_job_id` only through the application/coordinator path.
- [ ] Keep automatic exception retry inside one Job: `mark_failed` queues it with `next_run_at` while `attempt < max_attempts`; it never resets attempt and never creates a new execution.
- [ ] Run:

```bash
cd backend && python -m pytest \
  tests/test_task_runner.py::test_user_retry_creates_new_execution_and_preserves_failed_job \
  tests/test_task_runner.py::test_lease_takeover_increments_attempt \
  tests/test_task_runner.py::test_expired_lease_at_attempt_limit_fails_without_handler \
  tests/test_api.py -q
```

- [ ] Commit with subject `Create immutable job history for retries`.

### Task 7: Introduce the execution coordinator and cancellation token

**Files:**
- Add: `backend/app/infrastructure/execution_state.py`
- Modify: `backend/app/infrastructure/task_runner.py`
- Modify: `backend/app/application/compare_tasks.py`
- Modify: `backend/app/services/compare_service.py`
- Modify: `backend/app/services/pipeline.py`
- Test: `backend/tests/test_task_runner.py`
- Test: `backend/tests/test_pipeline.py`
- Test: new `backend/tests/test_execution_state.py`

- [ ] Add failing tests for persistence-before-memory, repeated token reads without filesystem reads, QUEUED cancellation, CANCEL_REQUESTED cancellation, idempotent CANCELLED, stale owner, expired lease, multiple Worker threads racing to claim one Job, and cancel-vs-success/failure Barriers.
- [ ] Add explicit cancellation-timing tests for: queued before claim, immediately before a stage, while a stage is blocked, immediately after a stage returns, and immediately before terminal commit. Every case must end as Job CANCELLED and Task `FAILED/CANCELLED`, with no success publication.
- [ ] Add immutable execution handoff types:

```python
@dataclass(frozen=True)
class TaskExecutionContext:
    job_id: str
    task_id: str
    worker_id: str
    cancellation_token: "CancellationToken"

@dataclass(frozen=True)
class CancellationToken:
    job_id: str
    worker_id: str
    coordinator: "ExecutionStateCoordinator"

    def raise_if_cancelled(self) -> None:
        self.coordinator.raise_if_cancelled(self.job_id, self.worker_id)
```

- [ ] `ExecutionStateCoordinator` owns one process-wide `threading.RLock` and an immutable in-memory Job snapshot map hydrated from disk. Every mutation must follow `validate -> persist -> replace snapshot`; a persistence exception must leave the snapshot unchanged.
- [ ] Require `mark_succeeded` and `mark_failed` to see RUNNING, current owner, and valid lease. Require `mark_cancelled` to accept ownerless QUEUED or current-owner CANCEL_REQUESTED, and treat CANCELLED as idempotent. Raise `TaskStaleLeaseError` without writes for stale owner/lease.
- [ ] Change `TaskHandler` from `Callable[[Mapping[str, Any]], None]` to `Callable[[TaskExecutionContext, Mapping[str, Any]], CompareTask | None]` and pass the context Runner → application → `CompareService.compare()` → `PipelineContext`.
- [ ] Add cancellation checks before and after every stage, inside the progress callback, and immediately before terminal commit. A remote OCR request may return, but its result must not start a downstream stage after cancellation.
- [ ] Catch `TaskCancelled` separately and call `mark_cancelled`; catch `TaskStaleLeaseError` only to log and stop; do not route either through generic failure.
- [ ] Run all coordinator/pipeline tests, including Barrier tests 20 times to expose races:

```bash
cd backend && python -m pytest tests/test_execution_state.py tests/test_pipeline.py tests/test_task_runner.py -q
cd backend && for i in $(seq 1 20); do python -m pytest tests/test_execution_state.py -q || exit 1; done
```

- [ ] Commit with subject `Coordinate cancellation and terminal transitions`.

### Task 8: Make terminal persistence authoritative and SSE terminal-sticky

**Files:**
- Add: `backend/app/infrastructure/reconciliation.py`
- Modify: `backend/app/infrastructure/execution_state.py`
- Modify: `backend/app/services/pipeline.py`
- Modify: `backend/app/services/compare_service.py`
- Modify: `backend/app/services/progress_bus.py`
- Modify: `backend/app/application/compare_tasks.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_execution_state.py`
- Test: `backend/tests/test_pipeline.py`
- Test: new `backend/tests/test_progress_bus.py`
- Test: new `backend/tests/test_reconciliation.py`

- [ ] Add failing order tests recording calls and asserting:

```python
assert calls == ["persist_task", "persist_job", "publish_terminal"]
```

and that a queued terminal event cannot be replaced or removed by later progress.
- [ ] Remove terminal publication and terminal Task persistence from pipeline stages and `CompareService`. The pipeline returns a result; only the coordinator commits Task then Job then publishes the Task revision while holding its lock.
- [ ] Record `terminal_job_id` and `terminal_attempt` in the same Task terminal update. Cancellation winning the coordinator lock must prevent later success/failure writes.
- [ ] On the first successful result completion, set `report_revision` from 0 to 1 in the same terminal Task mutation. A replay/reconciliation of that terminal must not increment it again.
- [ ] Close the Task-terminal/Job-nonterminal crash window in this deployable batch: add a minimal idempotent startup reconciliation that detects a terminal Task whose `terminal_job_id`/`terminal_attempt` match a nonterminal Job and completes that Job before workers start. Add a failure-injection test where Task persistence succeeds, Job persistence raises, then startup reconciliation repairs the Job without republishing or changing the Task terminal. Batch 4 extends this same service with orphan, missing-active, restart-lease, and legacy-layout cases.
- [ ] Change each progress subscriber to a capacity-one queue with these rules: nonterminal replaces pending nonterminal; terminal replaces pending progress; a pending terminal is never replaced by any later event. Transport disconnect remains recoverable through polling.
- [ ] Run:

```bash
cd backend && python -m pytest \
  tests/test_execution_state.py tests/test_pipeline.py tests/test_progress_bus.py tests/test_reconciliation.py -q
```

- [ ] Commit with subject `Publish terminal progress after durable commit`.

### Task 9: Implement the frontend SSE-to-polling state machine

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/hooks.ts`
- Modify: `frontend/src/lib/api_sse.ts`
- Modify: `frontend/src/pages/ResultPage.tsx`
- Modify: `frontend/src/pages/ComparisonRecordsPage.tsx`
- Test: `frontend/src/lib/api_sse.test.ts`
- Test: `frontend/src/pages/ResultPage.test.tsx`
- Test: `frontend/src/pages/ComparisonRecordsPage.test.tsx`

- [ ] Add failing fake-timer tests for initial load, first-event timeout, SSE error/EOF, polling network errors, terminal revision confirmation, cancellation text, and unmount cleanup.
- [ ] Add `terminal_reason`, `revision`, and `report_revision` to Task/record types. Display `FAILED + CANCELLED` as “已取消” and suppress Retry; display retry only for `EXECUTION_FAILED` and eligible `SUBMISSION_FAILED`.
- [ ] Implement one explicit hook state machine:

```ts
type ProgressMode = "INITIAL_LOAD" | "SSE" | "POLLING" | "TERMINAL";
```

Start SSE after initial load; on error, EOF, or first-event timeout, close it and poll with bounded backoff. Treat a terminal event as a hint until `getTask()` returns a terminal Task with `task.revision >= event.revision`.
- [ ] Keep polling after transient HTTP errors. On every effect cleanup, close the stream, abort in-flight reads, and clear all timers.
- [ ] Run:

```bash
cd frontend && env -u VITE_OIDC_AUTHORITY -u VITE_OIDC_CLIENT_ID npm test -- \
  src/lib/api_sse.test.ts src/pages/ResultPage.test.tsx src/pages/ComparisonRecordsPage.test.tsx
```

- [ ] Commit with subject `Converge task progress through SSE and polling`.

### Task 10: Correct final Diff deduplication

**Files:**
- Modify: `backend/app/services/pipeline_stages.py`
- Modify: `backend/app/models.py` only if a stable path accessor is required
- Test: `backend/tests/test_pipeline.py`
- Test: relevant golden files only when the focused regression proves they encode the bug

- [ ] Add failing tests for same-ID merge, cross-page equal text, cross-clause equal text, no evidence, coverage 0.79, coverage 0.80 inclusive, both-side AND semantics, and reversed input order.
- [ ] Define and use:

```python
FINAL_DIFF_EVIDENCE_OVERLAP_THRESHOLD = 0.80

def coverage(left: BBox, right: BBox) -> float:
    return intersection_area(left, right) / min(area(left), area(right))
```

- [ ] Different IDs may merge only when source, section/type, clause ID or stable section path, diff type, original text, and compare text all match and located evidence is on the same page with coverage `>= 0.80`. If both sides have evidence, both sides must pass. Unlocated different IDs never text-merge.
- [ ] Choose the lexicographically smallest nonempty ID independent of input order. Merge evidence, flags, sources, and review projection, and remap OCR/review/AuditItem references to the canonical ID.
- [ ] Run focused tests and any directly affected golden suite. If a golden changes, inspect the fixture diff manually and record the approved rule in the test name/fixture note; do not regenerate unrelated snapshots.
- [ ] Commit with subject `Require spatial evidence for final diff dedupe`.

### Task 11: Make AuditItem the canonical review unit

**Files:**
- Modify: `backend/app/services/audit_summary.py`
- Modify: `backend/app/services/review_service.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/api_schemas.py`
- Modify: `backend/app/api_presenters.py`
- Modify: `backend/app/api.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/ResultPage.tsx`
- Test: `backend/tests/test_audit_summary.py`
- Test: `backend/tests/test_api.py`
- Test: `frontend/src/pages/ResultPage.test.tsx`

- [ ] Change the existing “skip untyped evidence” test to expect one fallback AuditItem with `quality_status="NEEDS_REVIEW"`, `review_flags` containing `EVIDENCE_UNLOCATED` when coordinates are missing, and a stable ID.
- [ ] Add failing tests for typed ADD/DELETE/MODIFY splitting, legacy Diff-review broadcast to all child items, partial canonical map fill-only behavior, UNREVIEWED omission, mixed Diff projection, one increment per request, and sibling-card independence.
- [ ] Extend the immutable AuditItem DTO with section/source/text/evidence/quality/confidence/OCR/remediation/review fields needed by the frontend and report. `build_audit_items()` must return at least one item per Diff.
- [ ] Normalize legacy review data on read: generate all items; apply an existing canonical item review first; broadcast legacy Diff review fields only to missing child items; do not create entries for UNREVIEWED. The next successful write persists the complete normalized map.
- [ ] Make `audit_item_reviews` the sole write source. The Diff endpoint bulk-writes all child items in one locked Task mutation and owns the review-triggered `report_revision += 1`; a successful request increments exactly once regardless of child count. Project the aggregate Diff status: all equal → that state; mixed → NEEDS_REVIEW; untouched → UNREVIEWED.
- [ ] Make stats always count generated AuditItems and return `review_unit="audit_item"`.
- [ ] Return this shape from a single-item review:

```python
class AuditItemReviewUpdateResponse(BaseModel):
    task_id: str
    audit_item: AuditItemResponse
    review_stats: ReviewStatsResponse
    report_revision: int
```

- [ ] Have the frontend consume backend-normalized `audit_items`; remove its independent reconstruction logic. Update one returned item and its statistics without mutating sibling items.
- [ ] Run:

```bash
cd backend && python -m pytest tests/test_audit_summary.py tests/test_api.py tests/test_report_generator.py -q
cd frontend && env -u VITE_OIDC_AUTHORITY -u VITE_OIDC_CLIENT_ID npm test -- src/pages/ResultPage.test.tsx src/lib/api.test.ts
```

- [ ] Commit with subject `Use audit items as the review source of truth`.

### Task 12: Complete report and API evidence fields

**Files:**
- Modify: `backend/app/api_schemas.py`
- Modify: `backend/app/api_presenters.py`
- Modify: `backend/app/services/report_generator.py`
- Test: `backend/tests/test_report_generator.py`
- Test: `backend/tests/test_api.py`

- [ ] Add failing API assertions for `section_type`, `section_path`, `match_confidence`, and `structural_flags`.
- [ ] Add report tests proving located and unlocated AuditItems both appear with source, section, original/compare text, evidence or “未定位”, quality flags, confidence, OCR/remediation context, and review status/comment.
- [ ] Render every canonical AuditItem except items explicitly reviewed as IGNORED. Do not silently omit items without evidence.
- [ ] Rerun targeted API/report tests and commit with subject `Render complete audit evidence in reports`.

### Task 13: Batch 2 verification and two-flow SubAgent gate

- [ ] Run the full backend/frontend gate from Task 4 and record exact counts.
- [ ] Spawn two independent SubAgents in parallel:
  1. execution reviewer: state transitions, Retry identity, token propagation, races, terminal ordering, SSE;
  2. diff/audit reviewer: 0.80 AND dedupe rule, canonical IDs/remaps, legacy review projection, stats, report/frontend behavior, every golden change.
- [ ] Give each reviewer the confirmed design sections, Batch 2 commit range, and explicit instruction to report file:line findings without edits.
- [ ] Fix blocking/high findings test-first, rerun the full gate, and request focused re-review. Do not start Batch 3 until both reviewers report no blocking findings.

---

## Batch 3 — Submission and Report Reliability

### Task 14: Stream uploads through staging and scoped compensation

**Files:**
- Modify: `backend/app/api.py`
- Modify: `backend/app/application/compare_tasks.py`
- Modify: `backend/app/utils/file_utils.py`
- Modify: `backend/app/infrastructure/artifact_store.py`
- Add: `backend/app/infrastructure/atomic_files.py`
- Add: `backend/app/infrastructure/recovery_store.py`
- Add: `backend/scripts/recover_submissions.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api.py`
- Test: `backend/tests/test_file_utils.py`
- Test: `backend/tests/test_artifact_store.py`
- Add: `backend/tests/test_recovery_store.py`

- [ ] Add failure-injection tests for upload size overflow while streaming, second-file validation failure, artifact publish failure, Task save failure, Job create failure, enqueue failure, compensation failure, recovery-marker creation failure, recovery retry-count update failure, and retry-enqueue/worker-claim ordering. Marker-write failures must assert the CRITICAL event and preservation of the primary submission error.
- [ ] Stream each upload into a unique Task-scoped staging directory while counting bytes; validate both complete staged files before publishing either final input artifact.
- [ ] Implement an idempotent compensation ledger that deletes only paths created by the current submission attempt. Preserve the primary exception and log cleanup exceptions separately.
- [ ] On Job creation failure, persist Task `FAILED/SUBMISSION_FAILED`, retain validated input artifacts, and remove staging/temp Job files. On retry enqueue failure, restore an explainable failed Task state.
- [ ] Add a recovery marker at `storage/recovery/{task_id}.json` even if Task persistence failed. Marker creation and retry-count updates must call `atomic_write_json`; if marker writing also fails, emit a CRITICAL structured event.
- [ ] Implement one idempotent recovery service used both by `recover_submissions.py` and application startup. It retries only the scoped compensation actions recorded in each marker, increments the marker attempt count after a failed retry, and deletes the marker only after every action succeeds. Add `test_recovery_retry_success_removes_marker()` and `test_recovery_retry_failure_preserves_primary_error_and_updates_attempt()`.
- [ ] Implement the first atomic primitive now because recovery markers and Batch 3 reports require it:

```python
def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
```

`atomic_write_text` must use a unique same-directory temp file, flush/fsync, `os.replace`, parent-directory fsync, and best-effort temp cleanup.
- [ ] Run targeted tests:

```bash
cd backend && python -m pytest \
  tests/test_api.py tests/test_file_utils.py tests/test_artifact_store.py tests/test_recovery_store.py -q
```

- [ ] Commit with subject `Make comparison submission compensating and recoverable`.

### Task 15: Version and serialize report generation

**Files:**
- Add: `backend/app/infrastructure/report_store.py`
- Modify: `backend/app/application/compare_tasks.py`
- Modify: `backend/app/services/report_generator.py`
- Modify: `backend/app/infrastructure/artifact_store.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/api.py`
- Test: `backend/tests/test_report_generator.py`
- Test: `backend/tests/test_api.py`
- Add: `backend/tests/test_report_store.py`

- [ ] Add failing concurrent tests proving same `(task_id, report_revision)` calls the generator once, different revisions do not share a lock, review creates a new filename, failure preserves prior report, and temp files are removed.
- [ ] Keep `task.revision` incrementing on every Task persistence; an SSE event only carries the revision that was already persisted and never increments it itself. Reuse the `report_revision` established by Batch 2: completion/review paths own its increments, while report generation, progress updates, and report-path persistence never increment it.
- [ ] Name reports `contract_compare_report-r{report_revision}.pdf`.
- [ ] Implement a ref-counted `ReportLockRegistry` keyed by `(task_id, report_revision)`. Under the per-key lock, double-check the manifest and final file, generate to a unique temp, fsync, and atomically publish. Remove a key when its last waiter exits.
- [ ] Run report concurrency tests repeatedly:

```bash
cd backend && for i in $(seq 1 20); do python -m pytest \
  tests/test_report_store.py tests/test_report_generator.py tests/test_api.py -q || exit 1; done
```

- [ ] Commit with subject `Generate reports once per report revision`.

### Task 16: Provide production quality cases safely


- [ ] Add failing tests for an empty volume, idempotent second initialization, no overwrite of an administrator case, invalid `expected.json`, missing `tests/`, export append behavior, and target/seed equality or parent-child conflicts.
- [ ] Configure production defaults:


### Task 17: Add abortable frontend HTTP and hermetic OIDC tests

**Files:**
- Modify: `frontend/src/lib/authFetch.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/hooks.ts`
- Modify: `frontend/src/test/setup.ts`
- Test: `frontend/src/lib/authFetch.test.ts`
- Test: `frontend/src/lib/api.test.ts`
- Test: relevant page tests

- [ ] Add fake-timer tests for request timeout, caller AbortSignal, merged timeout/caller cancellation, unmount abort, and a test process started with local OIDC env values.
- [ ] Give read APIs a bounded timeout and accept an optional caller signal. Ensure timeout and component cleanup both abort the underlying authorized fetch without converting an intentional abort into a user-visible server error.
- [ ] Make test setup explicitly clear OIDC variables per test and restore them afterward; do not depend on the developer’s `.env`.
- [ ] Run frontend tests both with and without hostile OIDC env values:

```bash
cd frontend && env -u VITE_OIDC_AUTHORITY -u VITE_OIDC_CLIENT_ID npm test
cd frontend && VITE_OIDC_AUTHORITY=https://invalid.example VITE_OIDC_CLIENT_ID=local npm test -- src/auth/config.test.ts src/pages/ResultPage.test.tsx
```

- [ ] Commit with subject `Abort stale frontend requests deterministically`.

### Task 18: Batch 3 verification and SubAgent gate

- [ ] Run the full gate plus repeated `test_report_store.py` and recovery failure-injection tests.
- [ ] Spawn two independent SubAgents in parallel: one reviews upload/compensation/recovery markers; one reviews report revision/locking, production quality seeding, and frontend abort/OIDC isolation.
- [ ] Fix blocking/high findings test-first and obtain focused re-reviews before Batch 4.

---

## Batch 4 — Single-Process Deployment Consistency

### Task 19: Migrate all metadata publication to common atomic files

**Files:**
- Modify: `backend/app/infrastructure/atomic_files.py`
- Modify: `backend/app/infrastructure/task_repository.py`
- Modify: `backend/app/infrastructure/task_runner.py`
- Modify: `backend/app/infrastructure/artifact_store.py`
- Modify: `backend/app/infrastructure/recovery_store.py`
- Modify: `backend/app/infrastructure/report_store.py`
- Test: new `backend/tests/test_atomic_files.py`
- Test: `backend/tests/test_task_repository.py`
- Test: `backend/tests/test_artifact_store.py`

- [ ] Add failure-injection tests at write, file fsync, replace, and parent fsync; assert temp cleanup and preservation of the last valid target. Add a corrupt-manifest recovery test.
- [ ] Complete `atomic_write_text`, `atomic_write_json`, and `atomic_publish_file`. They must have no manifest side effect.
- [ ] Route Task, Job, artifact/recovery/quality metadata, report manifests, and unified manifest writes through the common primitives. Manifest updates occur through one coordinator/store function; remove direct `write_text` and fixed `.tmp` paths for mutable metadata.
- [ ] Preserve source-of-truth ordering: Task write succeeds first; summary-index/manifest failure is logged and recoverable but does not roll back the Task.
- [ ] Run atomic and repository tests; commit with subject `Publish local metadata atomically`.

### Task 20: Reconcile crash windows and legacy Job layouts

**Files:**
- Modify: `backend/app/infrastructure/reconciliation.py`
- Modify: `backend/app/infrastructure/task_runner.py`
- Modify: `backend/app/infrastructure/execution_state.py`
- Modify: `backend/app/main.py`
- Add: `backend/scripts/reconcile_storage.py`
- Modify: `backend/tests/test_reconciliation.py`
- Test: `backend/tests/test_task_runner.py`

- [ ] Add separate failing tests for all startup inputs:
  1. terminal Task points to matching nonterminal Job;
  2. nonterminal Job is referenced by neither `active_job_id` nor `terminal_job_id`;
  3. PROCESSING Task has missing/mismatched active Job;
  4. PROCESSING Task has an old RUNNING Job with attempts remaining;
  5. the same at max attempts;
  6. runtime valid lease must remain unchanged;
  7. legacy/new duplicate execution agrees;
  8. duplicate execution conflicts.
- [ ] Merge `job.json` and `jobs/*.json` into one repository view while retaining each loaded Job’s source path. Update legacy execution 1 in place. Retry starts at `jobs/2.json`.
- [ ] If `job.json` and `jobs/1.json` agree on identity/status/attempt, de-duplicate. If they conflict, emit `DUPLICATE_JOB_EXECUTION` and make neither claimable.
- [ ] Extend the Batch 2 reconciliation service and run the complete reconciliation after the singleton lock is acquired but before worker threads start:
  - complete matching Job from terminal Task using `terminal_job_id/terminal_attempt`;
  - fail orphan nonterminal Job with `ORPHANED_JOB`;
  - fail missing/mismatched active Task as `SUBMISSION_FAILED` and create a recovery marker;
  - treat every pre-start RUNNING owner as stale, queue it if attempts remain, otherwise fail Job and Task with `PROCESS_RESTART_MAX_ATTEMPTS`.
- [ ] Make reconciliation idempotent and route every repair through atomic/coordinator writes. The standalone script uses the same service.
- [ ] Run `test_reconciliation.py` twice against the same fixture tree and commit with subject `Reconcile task and job crash windows`.

### Task 21: Add the comparison-record summary index

**Files:**
- Add: `backend/app/infrastructure/task_index.py`
- Modify: `backend/app/infrastructure/task_repository.py`
- Modify: `backend/app/api.py`
- Add: `backend/scripts/rebuild_task_index.py`
- Test: `backend/tests/test_task_repository.py`
- Test: `backend/tests/test_api.py`
- Add: `backend/tests/test_task_index.py`

- [ ] Add failing tests that instrument Task loads and prove one records page does not deserialize every Task/Diff. Add index-update failure and rebuild tests.
- [ ] Persist a compact comparison summary index containing only record-list fields. The Task JSON remains source of truth. Update the index after a successful Task write; log index failure without rolling back Task.
- [ ] Store the index at `storage/indexes/compare_records.json` with `schema_version`, `updated_at`, and a `records` map keyed by `task_id`; never copy `diffs`, OCR payloads, or contract text into it.
- [ ] Make the list API sort/filter/page from the summary index and load only the requested page’s Task details when needed.
- [ ] Rebuild deterministically from valid compare Task files while skipping corrupt and historical extraction payloads.
- [ ] Run pagination/index tests and commit with subject `Index comparison records for bounded pagination`.

### Task 22: Enforce one API process and structured execution logs

**Files:**
- Add: `backend/scripts/run_api.py`
- Add: `backend/app/infrastructure/runtime_lock.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/logging_config.py`
- Modify: `backend/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `docs/deployment.md`
- Modify: `docs/docker-deployment.md`
- Add: `backend/tests/test_run_api.py`
- Add: `backend/tests/test_runtime_lock.py`
- Add: `backend/tests/test_logging_config.py`
- Test: `backend/tests/test_task_runner.py`

- [ ] Add parameterized wrapper tests for `API_WORKERS`, `WEB_CONCURRENCY`, and `UVICORN_WORKERS`: absent/`1` succeeds; non-integer, zero, values above one, and conflicting values exit before uvicorn with `MULTI_API_PROCESS_UNSUPPORTED`.
- [ ] Make `run_api.py` the only documented/Docker startup command. It validates worker env before importing/starting uvicorn and always launches one API worker.
- [ ] Acquire a non-blocking `fcntl.flock(..., LOCK_EX | LOCK_NB)` advisory lock at `storage/runtime/api-singleton.lock` in lifespan before reconciliation and hold its file descriptor through shutdown. A second process must fail loudly with `MULTI_API_PROCESS_UNSUPPORTED`; `threading.RLock` remains the in-process state mechanism.
- [ ] Change logging to JSON with a stable event helper supporting:

```text
timestamp level event request_id task_id job_id execution_no attempt worker_id
from_status to_status terminal_reason task_revision report_revision error_type
error_code recovery_marker duration_ms
```

- [ ] Add failing log-contract tests asserting every event contains the complete stable field set, unavailable values serialize as JSON `null`, event names are stable, and claim/cancellation/terminal/stale-lease/compensation/report/reconciliation/index paths each emit their required event. Add a capture test asserting forbidden contract/OCR/token text and absolute paths are absent.
- [ ] Emit structured events for claim, cancellation request, terminal commit, stale lease, compensation failure, report reuse/generation, orphan/reconciliation, manifest recovery, and index rebuild.
- [ ] Start order must be: validate worker settings in wrapper → acquire singleton lock → ensure/init storage and quality cases → hydrate repositories/coordinator → reconcile → start workers/models → serve.
- [ ] Run wrapper/runtime/logging integration tests:

```bash
cd backend && python -m pytest \
  tests/test_run_api.py tests/test_runtime_lock.py tests/test_task_runner.py tests/test_logging_config.py -q
```

- [ ] Commit with subject `Enforce the single process runtime model`.

### Task 23: Final verification and per-flow SubAgent review

- [ ] Run every required gate from a clean test environment:

```bash
cd backend && python -m compileall app tests
cd backend && python -m ruff check .
cd backend && python -m pytest
cd frontend && env -u VITE_OIDC_AUTHORITY -u VITE_OIDC_CLIENT_ID npm test
cd frontend && npm run build
docker compose config
```

- [ ] Run race/recovery suites repeatedly:

```bash
cd backend && for i in $(seq 1 20); do python -m pytest \
  tests/test_execution_state.py tests/test_report_store.py tests/test_reconciliation.py -q || exit 1; done
```

- [ ] Run repository searches:

```bash
rg -n "class Extraction(Task|Field)|from app\.models_extraction|save_extraction_task|load_extraction_task|list_extraction_tasks|update_extraction_task" backend/app
rg -n "ExtractionField|VITE_ENABLE_EXTRACTION|EXTRACTION_MAX_(DOCUMENT|IMAGE)_SIZE_MB|TOGGLE_EXTRACTION_MENU" \
  frontend/src frontend/.env.example AGENTS.md docs/architecture.md docs/deployment.md docs/docker-deployment.md
rg -n "from app\.models_extraction|save_extraction_task|load_extraction_task|list_extraction_tasks" backend/tests
rg -n "uvicorn .*--workers|gunicorn" backend/Dockerfile docker-compose.yml backend/scripts
```

Expected: all four commands exit 1 with no current-surface or executable unsupported-deployment matches. The negative OpenAPI test may contain `/api/extract`, and deployment documentation may name forbidden multi-worker commands while explaining the restriction.

- [ ] Spawn three independent SubAgents in parallel:
  1. compare execution flow from POST through Job/Coordinator/Pipeline/SSE/cancel/retry/reconciliation;
  2. diff/audit/report flow including golden changes and report-revision locking;
  3. frontend/infrastructure/deployment flow including abort cleanup, index, atomic files, quality seed, wrapper, singleton lock, and structured-log privacy.
- [ ] Require severity, file:line evidence, reproducible scenario, and missing-test identification. Reviewers do not edit files.
- [ ] Fix all blocking/high findings test-first; rerun the relevant reviewer, the repeated race suite, and the full gate. Record accepted medium/low findings with rationale in the final handoff; do not silently ignore them.
- [ ] Inspect the final diff and working tree:

```bash
git status --short
git diff --check
git log --oneline --decorate -20
```

- [ ] Commit final review-only fixes, if any, with subject `Close stabilization review findings`.

## Completion Evidence

The implementation is complete only when all four batch gates are green, each batch has an independent no-blocker SubAgent verdict, the final three flow reviews have no unresolved blocking/high findings, no unauthorized working-tree files were staged, and the final handoff includes exact backend/frontend test counts, repeated race-suite results, build result, Docker configuration result, golden fixture changes with reviewer approval, and any explicitly accepted lower-severity limitations.
