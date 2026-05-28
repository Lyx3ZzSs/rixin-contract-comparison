# Contract Comparison Golden Cases

Each case directory contains `expected.json` and either:

- `actual.json` for deterministic offline metric checks, or
- `original.pdf` and `compare.pdf` for end-to-end evaluation through `CompareService`.

Expected diffs use stable semantic fields such as `diff_type`, `source_type`, `title_contains`, `original_contains`, and `compare_contains`.
