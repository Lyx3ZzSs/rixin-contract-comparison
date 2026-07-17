# Task 11 implementation report

## Outcome

- `AuditItem` is now the canonical review unit for reads, writes, statistics, reports, and the result-page audit cards.
- Every Diff produces at least one stable AuditItem. Typed evidence produces ADD/DELETE/MODIFY items; untyped or missing evidence produces a fallback item using `{canonical_diff_id}:{diff_type}`.
- Unlocated fallback items are marked `NEEDS_REVIEW` with `EVIDENCE_UNLOCATED`.
- The single-item API returns `task_id`, the complete normalized `audit_item`, AuditItem-based `review_stats`, and `report_revision`.
- The compatibility Diff review API writes every child AuditItem in one repository mutation and increments `report_revision` once per successful request.

## TDD record

RED was observed before production changes for:

- fallback generation, stable IDs, and unlocated evidence quality;
- legacy Diff review broadcast, canonical-map precedence, partial fill, and UNREVIEWED omission;
- normalized AuditItems and normalized counts on read without persistence;
- complete single-item response, sibling independence, mixed Diff projection, and revision increments;
- Diff bulk review and AuditItem-based statistics;
- frontend replacement of one returned item without changing siblings;
- independent review-state badges;
- quality-summary `total_count` and `review_unit`;
- legacy review visibility in reports.

GREEN coverage includes concurrency and failure injection:

- concurrent reviews of two siblings both persist under the repository lock and increment report revision independently;
- a forced authoritative write failure leaves the prior map, normalization marker, revision, statistics, and Diff projection unchanged;
- unknown item IDs do not mutate the Task;
- resetting to UNREVIEWED removes the canonical map entry while retaining normalized-map semantics.

## Normalization precedence

1. Generate the complete AuditItem set for every Diff.
2. Keep valid non-UNREVIEWED canonical `audit_item_reviews` entries first.
3. For historical, not-yet-normalized Tasks only, broadcast a non-UNREVIEWED legacy Diff review to missing children.
4. Never store UNREVIEWED map entries.
5. On the next successful review write, persist the normalized non-UNREVIEWED map and set `audit_item_reviews_normalized=true` in the same locked mutation.

The explicit marker is required because an omitted key means UNREVIEWED after normalization, while an old partial map without the marker still requires legacy fill. Reads never persist this migration projection.

## Diff projection and revision transaction

- all child statuses equal: project that status;
- mixed child statuses: project `NEEDS_REVIEW`;
- all children untouched: project `UNREVIEWED`;
- comments/actor/timestamp project only when all child review records are identical; otherwise compatibility metadata is blank;
- every successful item or Diff-level review request increments `report_revision` exactly once, including repeated requests;
- a Diff-level request increments once regardless of child count.

## Frontend policy

- `ResultPage` consumes backend-normalized `task.audit_items` and no longer creates semantic AuditItems from Diff evidence.
- A successful review replaces only the response item by ID, then replaces statistics and report revision from the response.
- A legacy response without `audit_items` uses the conservative empty-list fallback instead of reconstructing a second semantic model.
- Each card displays its own independent status badge.

## Review fixes

- Read-only compatibility projection now derives each Diff review state from normalized AuditItems for `GET /diffs` and quality-summary reads. The projection deep-copies Diffs, does not persist normalization, and does not increment either task or report revisions.
- AuditItem responses now carry immutable item-level OCR and remediation context. Context is associated by exact `diff_id`, uses stable ordering, is inherited by every typed child item of that Diff, and defaults conservatively for historical tasks.
- Concurrent frontend saves track loading by AuditItem ID. Every successful response replaces its returned item by ID, while aggregate counts and `report_revision` are accepted only when the response revision is not older than the current task revision.
- Comparison-axis markers now require `evidence_state=LOCATED`, a positive page number, finite bounding-box coordinates, and a positive-area box. Invalid or unlocated AuditItems remain visible in the audit panel without producing markers.
- Deferred-response tests cover out-of-order successful sibling writes and a failed sibling write without clearing another item's loading state.

## Intentional golden updates

- Audit summary: a no-evidence DELETE Diff increases the AuditItem total instead of disappearing.
- Report: the unlocated `D004` table Diff is now present.
- Result page: fallback changes total AuditItems from 4 to 5 and DELETE items from 1 to 2; the unlocated card is visible while no axis marker is created.

## Verification

- Backend focused: `73 passed`.
- Backend full: `1299 passed`.
- Frontend focused: `48 passed`.
- Frontend full: `154 passed`.
- Frontend production build: passed.
- Ruff check: passed.
- Ruff format check for all seven touched Python files: passed. The repository-wide check still reports 114 pre-existing formatting differences outside this task's scope.
- Python compileall: passed.
- `git diff --check`: passed.

Known baseline warnings are unchanged: SWIG deprecation warnings and the TestClient/httpx2 migration warning.
