# OCR Compare Cases

Each child directory is one scanned contract comparison case.

Required files for committed deterministic tests:

- `expected.json`: human-reviewed expected output.
- `actual.json`: saved `CompareTask` payload used by tests that do not call OCR services.
- `README.md`: case notes and known difficulty tags.

Optional files for local or internal full runs:

- `original.pdf`
- `compare.pdf`

Sensitive real contracts must not be committed. For sensitive cases, commit only annotation files and load PDFs from an internal path when running the evaluator locally.
