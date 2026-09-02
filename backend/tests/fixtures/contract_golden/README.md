# Contract comparison Golden corpus

Baseline version: `contract-golden-v1`

This directory is the single human-reviewed semantic truth source for the 10-contract blocking regression gate. The sealed baseline contains 10 contracts, 20 immutable PDF hashes and 186 semantic assertions.

The PDFs are private test data and are deliberately not stored here. CI or a developer must provision a versioned bundle and set:

```bash
export GOLDEN_CONTRACT_DATA_ROOT=/secure/path/contract-golden
```

The bundle layout and immutable SHA256/page-count metadata are defined by `manifest.json`. Missing, replaced, or malformed PDFs fail the suite; the tests never skip.

From the repository root, run the complete blocking workflow:

```bash
make golden-contract
```

It verifies the private bundle, replays all 10 contracts, applies the semantic blocking oracle, and checks whether protected Golden files changed. The first baseline addition or a later approved Golden update must explicitly use:

```bash
GOLDEN_BASELINE_CHANGE_APPROVED=1 make golden-contract
```

This variable only acknowledges that humans reviewed the displayed semantic Golden diff. It never generates, updates, or overwrites the manifest or oracle.

Oracle files contain stable `case_id` values and semantic assertions. They intentionally do not freeze runtime diff IDs, ordering, OCR confidence, diagnostics prose, or exact pixel coordinates. There is no automatic Golden update command.

## CI contract

CI remains platform independent. It only needs to mount the private bundle, export `GOLDEN_CONTRACT_DATA_ROOT`, and execute `make golden-contract`. Missing data, a hash/page mismatch, fewer than 10 executed contracts, a skipped blocking test, a positive/negative/review mismatch, an unexpected confirmed diff, or a bbox regression returns a non-zero exit code.

The checker compares working-tree changes with `HEAD`. In a clean checkout it compares `HEAD^...HEAD`; for a multi-commit change, CI should expose the target revision through the platform-neutral `GOLDEN_BASE_REF` environment variable so the complete review range is inspected.

## Protected baseline

The following files are high-risk review areas:

- `manifest.json`
- `oracle/*.json`
- `../../golden_contract/semantic_matcher.py`
- `../../golden_contract/test_contract_golden_blocking.py`
- `../../../scripts/check_golden_baseline_change.py`

The change checker prints added/removed cases plus decision, semantic, and bbox expectation changes. A failing Golden must be diagnosed as a code regression first. Golden may change only after a real business improvement is verified against the original contracts and the new truth is approved by a human reviewer.
