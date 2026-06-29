# Phase 5 Quality Regression Design

## Purpose

Phase 5 turns the OCR compare gold case workflow into a repeatable quality regression system. It should answer whether a code, prompt, OCR, or model-routing change improves or regresses contract comparison quality against human-approved gold cases.

This phase does not directly change the comparison algorithm. It creates the measurement and gating layer needed before precision-focused changes can be made safely.

## Goals

- Provide one regression command that runs the existing OCR compare evaluator, compares the result with a baseline, applies gates, and writes run artifacts.
- Support both CI-style quality gates and lightweight experiment comparison.
- Record enough run metadata to reproduce and compare quality results over time.
- Extend the existing JSON and HTML reporting surface without introducing a separate platform dependency.
- Keep the implementation local and script-driven so it can later be called from CI.

## Non-Goals

- Do not integrate Langfuse, Phoenix, or another observability platform in this phase.
- Do not build a frontend annotation or review workbench.
- Do not automatically tune model-routing thresholds.
- Do not change comparison, OCR, extraction, or matching algorithms.
- Do not require a real CI provider integration; the script only needs to be CI-friendly.

## Existing Foundation

Phase 4B added:

- `backend/scripts/export_ocr_compare_gold_case.py` for exporting task output into gold case directories.
- `backend/scripts/evaluate_ocr_compare_quality.py` for evaluating approved expected diffs against actual comparison output.
- Gold annotation states: `DRAFT`, `APPROVED`, and `REJECTED`.
- Structured evaluation details including matches, missed expected diffs, unexpected actual diffs, and evidence drift.
- HTML report sections for human review.
- `docs/ocr_compare_gold_case_workflow.md` for the manual gold case workflow.

Phase 5 should build on this script instead of duplicating the evaluator.

## Recommended Approach

Use a lightweight local regression runner:

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --output-dir .ocr-compare-quality/runs/<run_id> \
  --thresholds .ocr-compare-quality/quality_thresholds.json \
  --fail-on-regression
```

The runner orchestrates:

1. Evaluate current gold cases with `evaluate_ocr_compare_quality.py` logic.
2. Load an optional baseline report.
3. Compare aggregate and case-level metrics.
4. Apply absolute and relative gates.
5. Write machine-readable run artifacts and an HTML report.
6. Exit non-zero only when requested and gates fail.

This approach is preferred because it reuses the current evaluator, keeps the scope small, and supports both local experiments and future CI usage.

## Alternatives Considered

### CI Gate Only

This would add only a threshold wrapper around the existing evaluator. It is simpler, but it does not help compare experiments, inspect regressions, or preserve run metadata.

### Full Experiment Platform

This would integrate a platform such as Langfuse or Phoenix. It is powerful, but it adds infrastructure and data model decisions before the project has enough stable gold cases to justify that complexity.

### Recommended Lightweight Hybrid

The first Phase 5 version should support both gates and experiment comparison using local JSON and HTML artifacts. Platform integration can be revisited after the quality workflow is stable.

## Data Model

### Threshold Configuration

The runner should accept an optional JSON threshold file. Missing values fall back to conservative defaults.

Example:

```json
{
  "min_recall": 0.95,
  "min_precision": 0.9,
  "min_evidence_hit_rate": 0.9,
  "max_task_failure_count": 0,
  "max_false_positive_increase": 1,
  "max_false_negative_increase": 0,
  "max_recall_drop": 0.02,
  "max_precision_drop": 0.02,
  "max_evidence_hit_rate_drop": 0.02
}
```

### Run Summary

Each run should write a `run_summary.json` under the output directory:

```json
{
  "run_id": "20260629-153000-local",
  "created_at": "2026-06-29T15:30:00+08:00",
  "git": {
    "branch": "v0.0.2",
    "commit": "abc123",
    "dirty": true
  },
  "case_root": "tests/fixtures/ocr_compare_cases",
  "baseline_path": ".ocr-compare-quality/baselines/v0.0.2.json",
  "thresholds": {},
  "current_report_path": "quality.json",
  "baseline_comparison_path": "baseline_comparison.json",
  "status": "PASSED",
  "failed_gates": []
}
```

### Baseline Comparison

The runner should write `baseline_comparison.json`:

```json
{
  "aggregate_delta": {
    "precision": 0.01,
    "recall": -0.02,
    "evidence_hit_rate": 0.0,
    "false_positive_count": 1,
    "false_negative_count": 0,
    "task_failure_count": 0
  },
  "case_deltas": [
    {
      "case_id": "case_contract_amount",
      "precision_delta": -0.2,
      "recall_delta": 0.0,
      "false_positive_delta": 1,
      "false_negative_delta": 0,
      "evidence_hit_rate_delta": 0.0
    }
  ],
  "failed_gates": []
}
```

## Gates

Phase 5 should support two classes of gates.

### Absolute Gates

Absolute gates validate the current result on its own:

- `min_recall`
- `min_precision`
- `min_evidence_hit_rate`
- `max_task_failure_count`
- `max_false_positive_count`
- `max_false_negative_count`

### Regression Gates

Regression gates compare current metrics against the baseline:

- `max_recall_drop`
- `max_precision_drop`
- `max_evidence_hit_rate_drop`
- `max_false_positive_increase`
- `max_false_negative_increase`
- `max_task_failure_increase`

When no baseline is provided, only absolute gates should run. The report should clearly say that baseline comparison was skipped.

## CLI Behavior

`run_quality_regression.py` should support:

- `--case-root`: required gold case root.
- `--output-dir`: required output directory for run artifacts.
- `--baseline`: optional baseline quality report.
- `--thresholds`: optional threshold JSON file.
- `--run-id`: optional explicit run id.
- `--html-output`: optional override; defaults to `<output-dir>/html`.
- `--fail-on-regression`: exit with status `1` when gates fail.
- `--write-baseline`: optional path to copy the current `quality.json` as a new baseline.

Default output files:

```text
<output-dir>/quality.json
<output-dir>/baseline_comparison.json
<output-dir>/run_summary.json
<output-dir>/html/index.html
```

## HTML Report Enhancements

The existing OCR compare HTML report should gain a regression section when regression metadata is supplied:

- Run metadata: run id, git branch, git commit, dirty flag, created timestamp.
- Gate summary: passed or failed, failed gate names and values.
- Baseline comparison table for aggregate metrics.
- Case-level regression table sorted by highest risk:
  - recall drop
  - precision drop
  - false negative increase
  - false positive increase
  - evidence hit rate drop

The HTML report should still work when no baseline is supplied.

## Error Handling

- Missing `case_root` should fail fast with a clear message.
- Missing baseline path should fail unless the baseline argument is omitted.
- Malformed threshold JSON should fail before running evaluation.
- Missing metrics in an older baseline should be treated as unavailable, not as zero, to avoid misleading deltas.
- Evaluator task failures should be represented in the quality report and then evaluated by gates.

## Testing Strategy

Backend tests should cover:

- Running regression without a baseline.
- Running regression with a baseline and no regression.
- Detecting recall, precision, evidence hit rate, false positive, and false negative regressions.
- Absolute gate failures.
- Exit code behavior with and without `--fail-on-regression`.
- Run metadata generation with mocked git command output.
- Baseline writing with `--write-baseline`.
- HTML output includes gate summary and baseline deltas.

Existing evaluator tests should remain unchanged except where helper extraction is necessary.

## Documentation

Add a Phase 5 usage document that explains:

- How to create a baseline from approved gold cases.
- How to run local quality regression.
- How to read `run_summary.json`, `quality.json`, and `baseline_comparison.json`.
- How to interpret failures when gold case count is small.
- How to use the command from CI later.
- Why `DRAFT` and `REJECTED` expected diffs are not counted as gold.

## Success Criteria

Phase 5 is complete when a developer can run one command and answer:

- Did this change pass current quality gates?
- Did it improve or regress against the selected baseline?
- Which cases caused the regression?
- Was the run produced from a dirty or clean git state?
- Which JSON and HTML artifacts should be attached to a review or CI job?

The implementation should be committed on the current branch and keep local generated quality artifacts out of version control.
