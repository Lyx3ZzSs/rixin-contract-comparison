# Contract comparison A/B benchmark

This directory contains offline-only benchmark code. It does not change the
production extractor, matcher, API, UI, thresholds, or weights.

The runners discover retained PDFs under `storage/tasks`, identify documents
with SHA-256 prefixes, and write only aggregate metrics and anonymous case IDs.
They never copy contract text into result files.

Run the deterministic E0/E1, matcher, evidence, and table benchmark:

```bash
PYTHONPATH=backend python experiments/contract_ab/run_offline_benchmark.py
```

Run the full PaddleOCR-VL corpus after starting an OpenAI-compatible MLX-VLM
server. Raw backend output is kept only under the ignored `cache/` directory:

```bash
PYTHONPATH=backend /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
  experiments/contract_ab/run_full_paddleocr_vl.py --server-url http://127.0.0.1:18080/v1
```

Adapt completed full-corpus outputs and run the frozen downstream chain:

```bash
PYTHONPATH=backend:experiments/contract_ab python \
  experiments/contract_ab/analyze_full_extraction.py \
  --backends E0 MinerU PaddleOCR-VL
```

Build the matcher and table review artifacts:

```bash
PYTHONPATH=backend python experiments/contract_ab/build_matcher_conflicts.py
PYTHONPATH=backend:experiments/contract_ab python \
  experiments/contract_ab/build_table_conflicts.py
```

Full MinerU inference is run with its CLI against the ignored, hash-named
corpus staging directory. The experimental virtual environment and downloaded
backend outputs remain outside version control. Human-review artifacts contain
contract text and are therefore written only under ignored
`review_artifacts/private/`.
