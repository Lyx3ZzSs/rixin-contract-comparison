# Contract Heading Evidence Repair Design

## Goal

Systematically eliminate false clause-title differences caused by OCR title loss,
short-heading misclassification, and repeated scan overlays, while preserving real
high-risk `ADD` differences such as newly introduced confidentiality, termination,
force-majeure, intellectual-property, and liability clauses.

The design must fix the failure class represented by comparison task
`cd3da5ca-e867-49db-aaf9-c35eb245639e` without adding task-ID, filename, party-name,
or clause-title special cases.

## Background

The affected task demonstrates four independent failures that compound into one
user-visible symptom:

1. The Original PDF has a complete native text layer, but both sides are parsed by
   the structured OCR extractor. OCR recognizes parent numbers such as `8.` and
   `17.` but loses their titles.
2. The Compare scan recognizes complete titles. The matcher therefore sees parent
   clauses only on the Compare side even though corresponding child clauses match
   at or near 100%.
3. The quality filter intentionally retains high-risk heading additions when the
   opposite side contains only a bare number. This safety rule is correct when no
   stronger evidence exists, but it currently cannot consult the native PDF text.
4. Short top-level headings such as `13. 索赔` and `18. 份数` are classified as weak
   numeric markers despite being `paragraph_title` layout blocks. They are appended
   to the previous clause. A repeated `黄科` scan overlay also enters clause text.

The resulting false differences are not caused by failed body matching. The child
clauses and most parent bodies match correctly; the corruption occurs before or at
the clause-boundary stage.

## Scope

This change covers:

- conservative repair of top-level numbered headings using a PDF native text layer;
- removal of highly repeated short scan overlays from clause comparison;
- strong handling of short numbered `paragraph_title` blocks;
- native-evidence-backed suppression of residual title-only false differences;
- deterministic diagnostics and regression tests for all safety boundaries.

## Non-Goals

- Replacing structured OCR with PyMuPDF for an entire document.
- Reconstructing arbitrary missing body paragraphs from the native text layer.
- Changing matcher scoring, assignment, or semantic reranking.
- Relaxing protected-value or high-risk-heading safeguards without exact evidence.
- Adding task-specific rules or committing the supplied business contract as a test
  fixture.
- Changing existing `/api/compare/*` or `/api/extract/*` response contracts.

## Considered Approaches

### Quality-filter-only suppression

This is the smallest code change, but it hides symptoms after the clause tree is
already wrong. It does not restore parent section paths, does not split clauses 13
and 18, and does not remove scan overlays. It is rejected.

### Use native extraction for Original and OCR for Compare

This restores Original titles but introduces asymmetric layout, table, reading-order,
and evidence-box behavior. Existing alignment logic deliberately uses compatible
structured extraction on both sides. A wholesale side-specific switch has too large
a regression surface and is rejected.

### Layered evidence fusion

Structured OCR remains authoritative for document layout and body text. Native PDF
text is used only to repair an existing bare numbered heading when page, number,
geometry, and local structure agree. Clause splitting and quality filtering receive
their own bounded fixes. This approach addresses the root cause while retaining the
current safety posture and is selected.

## Architecture

The comparison data flow becomes:

```text
PDF
  -> structured OCR extraction
  -> native numbered-heading repair
  -> repeated overlay filtering
  -> document understanding
  -> clause splitting
  -> clause matching
  -> diff construction
  -> native-evidence quality fallback
```

The two post-extraction normalizers run after structured extraction alignment and
before document profiling and downstream clause processing. They do not call remote
models and do not alter public APIs.

## Component 1: Native Numbered-Heading Repair

Create `backend/app/services/native_heading_repair.py` with a focused service:

```python
class NativeHeadingRepairService:
    def repair(self, document: Document) -> NativeHeadingRepairResult: ...
```

`NativeHeadingRepairResult` contains repair decisions and warnings. The service may
mutate the supplied `Document` consistently with existing extraction cleanup
services, but every mutation must be represented by a decision record.

### Native candidate extraction

Use PyMuPDF spans or words from `Document.path`. Build a per-page index of candidate
top-level headings matching a normalized form equivalent to:

```text
<one- or two-digit number><heading punctuation><2-24 character title>
```

Reject candidates that are dates, amounts, quantities, page numbers, table values,
sentence-like body text, or punctuation-only text. Preserve the native page number,
bbox, text, and normalized number/title separately.

### Repair preconditions

A native candidate may repair OCR only when all of these conditions hold:

1. The OCR page contains an existing bare block for the same top-level number, such
   as `8.`. The service never invents a numbered block when OCR found no marker.
2. The native candidate bbox overlaps the OCR marker bbox or has a compatible line
   center and reading-order position.
3. OCR does not already contain a conflicting non-empty title for that number.
4. Local structure supports a heading boundary. At least one of these signals is
   required: a following `N.x` child clause, a title-layout region, or body content
   followed by the next top-level number in sequence.
5. No second native candidate for the same number is geometrically ambiguous.

### Repair output

Update the existing bare OCR block instead of inserting a duplicate block:

- set its text to the native full heading;
- expand its bbox to cover the native heading bbox;
- rebuild or estimate char boxes for the added title text;
- preserve the existing layout and reading-order identifiers;
- append `native_heading_repair` to `source`;
- add a semantic reason containing the normalized number and match reason.

Examples repaired by this rule include `2. 服务内容`, `8. 知识产权`, `13. 索赔`,
`17. 合同生效`, and `18. 份数`. A scanned Compare PDF whose native layer contains
only scanner branding produces no native heading candidates and remains OCR-based.

### Failure behavior

Failure to open a PDF, extract native text, or resolve a candidate is fail-open: keep
the OCR document unchanged and emit a warning or non-repair decision. Native repair
must never cause the comparison task to fail.

## Component 2: Repeated Overlay Filter

Create `backend/app/services/repeated_overlay_filter.py`:

```python
class RepeatedOverlayFilter:
    def apply(self, document: Document) -> RepeatedOverlayFilterResult: ...
```

The filter identifies short OCR blocks that are scan overlays rather than contract
content. A normalized text is eligible only when:

- compact length is between 2 and 12 characters;
- it occurs on at least five pages and at least 80% of document pages;
- it appears no more than twice per page;
- at least 80% of occurrences form a stable normalized-position cluster;
- it is not a number, date, amount, clause marker, party label, signature label, or
  recognized contract heading.

For a confirmed overlay, retain the block for evidence and diagnostics but set
`enter_clause_compare=False`, set `flow_role="noise"`, and record source
`repeated_overlay_filter`. This removes repeated names such as `黄科` from clause
text while retaining a legitimate one-page contact occurrence in ordinary cases.

The filter must emit the normalized text, page coverage, occurrence count, position
cluster ratio, affected block IDs, and rejection reason for near-threshold candidates.

## Component 3: Short Numbered-Heading Classification

Modify the existing heading detector and splitter without lowering the global
heading acceptance threshold.

A single top-level numeric marker with a 2-12 character title is a strong heading
when its block type is `paragraph_title`, `doc_title`, or `title`, unless an existing
date, amount, quantity, or value-continuation rule rejects it. Add an explicit
`strong_title_block` candidate signal.

The weak numeric marker cap must not apply solely because a strong title block has a
title shorter than four characters. Non-title text blocks remain subject to the
current weak-marker behavior. This separates `13. 索赔` and `18. 份数` while keeping
values such as `18份`, `(2)1`, `2026年`, and table quantities out of the clause tree.

Do not solve this by adding only `索赔` and `份数` to the business-term set. Those
terms may be included for semantic scoring, but block role and numeric context are
the general boundary signal.

## Component 4: Native-Evidence Quality Fallback

Extend boundary coverage with a cached, read-only native heading index. This layer
is a safety net for documents where extraction repair is unavailable or where old
debug artifacts are reprocessed.

### High-risk heading ADD

Retain the current rule that keeps high-risk heading additions by default. Suppress
one only when all conditions hold:

- the opposite PDF native layer contains the exact normalized number and title on
  the candidate page or one adjacent page;
- the opposite OCR document contains the same bare parent number;
- both sides contain child clauses for that parent number;
- the child clauses are structurally present and no body-level addition remains;
- the changed text contains no protected amount, percentage, date, duration, party,
  or signature value.

If native evidence is missing or ambiguous, retain the ADD.

### Title-only MODIFY

Suppress a residual title-only MODIFY only when:

- clause numbers are equal;
- matcher body similarity is at least `0.90`;
- the native Original contains the exact full title;
- the changed fragment is limited to the heading or a confirmed overlay;
- neither side contains an unpaired protected value or meaningful body change.

This fallback must not suppress a real heading rename or a new high-risk clause body.

## Pipeline Integration

Integrate both post-extraction services in `ExtractionStage.execute()` after
`_align_structured_extractions()` and before `_ensure_profile()`. Run services on each
side independently because native text availability and overlay frequency differ by
document.

The repaired `Document` objects continue through the existing document-understanding,
clause-splitting, matching, diff, signing-region, and report stages. No API schema or
task persistence migration is required.

The quality fallback reads `Document.path` lazily and caches native page indexes by
path and file metadata for the duration of one comparison process. Failure remains
fail-open.

## Diagnostics

Add deterministic debug artifacts through the existing compare debug writer:

- `native_heading_repair.json` with repaired and rejected candidates per side;
- `repeated_overlay_filter.json` with confirmed and rejected repeated texts;
- repair counts and overlay counts in the extraction/preparation summary;
- suppression decisions that state whether native evidence was found, ambiguous,
  or unavailable.

Do not include full document text in new diagnostics. Store only candidate heading
text, short overlay text, page numbers, block IDs, bboxes, normalized keys, and
decision reasons.

## Testing Strategy

All implementation tasks follow test-driven development.

### Native heading repair tests

Generate temporary PDFs with a native text layer and construct deterministic OCR
`Document` objects in tests. Cover:

- bare `8.` repaired to `8. 知识产权` with a following `8.1`;
- bare `17.` repaired to `17. 合同生效` with following body/list content;
- bare `18.` repaired to `18. 份数`;
- existing conflicting OCR title is not replaced;
- wrong-number, amount-like, duplicate, and ambiguous native candidates are rejected;
- image-only PDF and unreadable path are no-op rather than errors;
- repaired bbox, source, char boxes, and diagnostic reason are present.

### Repeated overlay tests

Cover a 10-page document with the same short overlay on nine pages, a single-page
contact name, a repeated party label, unstable positions, and a repeated heading.
Only the confirmed overlay may be excluded from clause comparison.

### Clause splitter tests

Cover independent clauses for `13. 索赔` and `18. 份数`, including their following
children/body. Preserve negative cases for `18份`, `(2)1`, years, amounts, table
values, and short numeric body continuations.

### Diff-quality tests

Cover:

- high-risk heading ADD suppressed with exact native heading plus matched children;
- the same ADD retained without native text;
- same number but different title retained;
- true high-risk heading with a new body retained;
- title-only MODIFY suppressed at body similarity `>= 0.90`;
- body or protected-value MODIFY retained;
- non-high-risk existing boundary-coverage behavior remains unchanged.

### Integration and acceptance

Build a generated, non-sensitive two-document fixture reproducing the asymmetric
native/OCR heading pattern. Verify the resulting clause tree and final diffs without
calling external OCR services.

As a local acceptance run, reprocess task
`cd3da5ca-e867-49db-aaf9-c35eb245639e` from its existing workspace artifacts. The
contract remains untracked and is not committed.

## Acceptance Criteria

The change is accepted when all of the following are true:

1. `2. 服务内容`, `8. 知识产权`, `9. 保密`, `11. 合同变更、终止`,
   `12. 不可抗力`, `13. 索赔`, `14. 违约责任`, `17. 合同生效`, and
   `18. 份数` no longer produce title-only false ADD/MODIFY differences in the
   affected task.
2. Clauses 13 and 18 are independent parent clauses on both sides.
3. Repeated `黄科` overlay text does not enter clause text or final differences.
4. Existing matched children such as 8.1, 9.1, 11.1, 12.1, 13.1, and 14.1 do not
   regress in assignment or body similarity.
5. A generated test where Compare genuinely adds `8. 知识产权` or
   `14. 违约责任` without native Original evidence still emits ADD.
6. Existing backend tests, focused regression tests, Ruff checks, and Python
   compile checks pass.
7. No API response schema changes and no sensitive contract fixtures are committed.

## Implementation Sequence

1. Add the native heading index and repair service with unit tests.
2. Integrate native repair after structured extraction and add diagnostics.
3. Add repeated overlay filtering and diagnostics.
4. Correct strong short-title classification in the clause splitter.
5. Add native-evidence quality fallback while preserving high-risk guards.
6. Add generated integration coverage and run the affected task as local acceptance.
7. Run focused tests, the full backend suite, Ruff, and compile checks.

Each step must be independently testable and committed separately during
implementation. Existing unrelated worktree changes must not be reverted or included
in these commits.
