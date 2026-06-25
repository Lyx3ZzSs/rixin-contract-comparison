# OCR Compare Cases

Each child directory is one scanned contract comparison case.

Required files for committed deterministic tests:

- `expected.json`: human-reviewed expected output.
- `actual.json`: saved `CompareTask` payload used by tests that do not call OCR services.
- `README.md`: case notes and known difficulty tags.

Optional files for local or internal full runs:

- `original.pdf`
- `compare.pdf`

Sensitive real contracts must not be committed. For sensitive cases, commit only annotation files and documentation if appropriate. When running a local or internal full evaluation, place untracked local copies or symlinks named `original.pdf` and `compare.pdf` inside the case directory. Do not commit sensitive PDFs or extracted payloads.

## Expected JSON Fields

- `case_id`: stable case identifier matching the directory name.
- `tags`: case traits such as `scanned`, `table_heavy`, `seal_page`, or `low_text_confidence`.
- `critical_fields`: reviewed fields that are sensitive to OCR errors.
- `expected_diffs`: reviewed diff expectations. Use `diff_type`, `source_type`, `title_contains`, `original_contains`, `compare_contains`, and `expected_evidence`.
- `quality_expectations`: case-specific expectations used by humans when reviewing the report.

## Smoke Command

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases \
  --output .ocr-compare-quality/ocr_compare_quality.json \
  --html-output .ocr-compare-quality/html \
  --fail-on-threshold
```
