# Approved Layout Regression Cases

This directory contains approved real-contract layout fixtures. Every case must
include `case.json` with the source SHA-256, approver, approval timestamp, PDF,
compressed PP-Structure response, compressed PP-OCR response, and reviewed
`expected.json`.

Import approved task artifacts with:

```bash
python scripts/import_layout_regression_cases.py ../storage/tasks tests/fixtures/layout_real_cases \
  --approved-by "<approver>" --promote-current
```

`--promote-current` creates the initial expected result from the current V3
algorithm. Review that file and the generated HTML report before merging it.
