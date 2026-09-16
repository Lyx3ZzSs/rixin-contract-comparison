# Contract comparison Golden corpus

Baseline version: `contract-golden-v3`

This directory is the semantic truth source for the 15-contract blocking regression gate. The sealed baseline contains 15 contracts, 30 immutable PDF hashes and 255 semantic assertions. The v3 additions were visually checked against the source PDFs by the agent under the user’s explicit promotion instruction; no database review flags were changed.

The PDFs are private test data and are deliberately not stored here. CI or a developer must provision a versioned bundle and set:

```bash
export GOLDEN_CONTRACT_DATA_ROOT=/secure/path/contract-golden
```

The bundle layout and immutable SHA256/page-count metadata are defined by `manifest.json`. Missing, replaced, or malformed PDFs fail the suite; the tests never skip.

From the repository root, run the complete blocking workflow:

```bash
make golden-contract
```

It verifies the private bundle, replays all 15 contracts, applies the semantic blocking oracle, and checks whether protected Golden files changed. The first baseline addition or a later approved Golden update must explicitly use:

```bash
GOLDEN_BASELINE_CHANGE_APPROVED=1 make golden-contract
```

This variable only acknowledges that humans reviewed the displayed semantic Golden diff. It never generates, updates, or overwrites the manifest or oracle.

Oracle files contain stable `case_id` values and semantic assertions. They intentionally do not freeze runtime diff IDs, ordering, OCR confidence, diagnostics prose, or exact pixel coordinates. There is no automatic Golden update command.

## CI contract

CI remains platform independent. It only needs to mount the private bundle, export `GOLDEN_CONTRACT_DATA_ROOT`, and execute `make golden-contract`. Missing data, a hash/page mismatch, fewer than 15 executed contracts, a skipped blocking test, a positive/negative/review mismatch, an unexpected confirmed diff, or a bbox regression returns a non-zero exit code.

The checker compares working-tree changes with `HEAD`. In a clean checkout it compares `HEAD^...HEAD`; for a multi-commit change, CI should expose the target revision through the platform-neutral `GOLDEN_BASE_REF` environment variable so the complete review range is inspected.

## Protected baseline

The following files are high-risk review areas:

- `manifest.json`
- `oracle/*.json`
- `../../golden_contract/semantic_matcher.py`
- `../../golden_contract/test_contract_golden_blocking.py`
- `../../../scripts/check_golden_baseline_change.py`

The change checker prints added/removed cases plus decision, semantic, and bbox expectation changes. A failing Golden must be diagnosed as a code regression first. Golden may change only after a real business improvement is verified against the original contracts and the new truth is approved by a human reviewer.


## 2026-09-08 v2 historical reviewed update

See [UPDATE-v2.md](UPDATE-v2.md) for evidence, changed truth, and remaining blocking issues. The v2 update keeps all 20 PDF hashes unchanged and does not imply that the current algorithm passes every assertion.

The following command describes the historical v2 replay. Its source task IDs do not match the current v2.1 manifest; do not use that old directory to claim validation of the new actual tasks. For v2.1 results, see [UPDATE-v2.1.md](UPDATE-v2.1.md).

Historical v2 command (requires the v2 baseline):

```bash
python backend/scripts/verify_golden_contract_replay.py \
  --results-root output/analysis/eight-task-fix/release \
  --data-root output/analysis/eight-task-fix/golden-inputs \
  --report output/analysis/golden-v2-update/replay-report.json
```

The verifier requires all completed result artifacts listed in the current manifest, matching business-code fingerprints and immutable input hashes. Missing data or any semantic failure returns non-zero. This is a separate offline check; it does not replace `make golden-contract`.


## Historical v2.1 update

[UPDATE-v2.1.md](UPDATE-v2.1.md) records the two user-selected completed tasks, unchanged PDF hashes, and 90 passing assertions. The remaining eight oracle files are unchanged.

## Current v3: five-task promotion (2026-09-08)

[UPDATE-v3.md](UPDATE-v3.md) records promotion of all five tasks, 56 new assertions, source-PDF review and verification results. [The intake](intake/2026-09-08-five-tasks/README.md) retains the 55 source observations and their review decisions. The local versioned private bundle is `storage/golden-intake/contract-golden-v3/` (relative to the repository root). Earlier bundles contain only ten contracts and cannot validate v3.

七条新增失败的根因修复、回归边界与最新验收记录见 [UPDATE-v3-root-fixes.md](UPDATE-v3-root-fixes.md)。
