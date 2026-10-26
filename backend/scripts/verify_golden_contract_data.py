from __future__ import annotations

import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = BACKEND_DIR / "tests"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from golden_contract.golden_loader import load_corpus, load_manifest, oracle_statistics  # noqa: E402


def main() -> int:
    manifest = load_manifest()
    corpus = load_corpus()
    stats = oracle_statistics(corpus)
    for index, contract in enumerate(corpus, start=1):
        print(
            f"[{index:02d}/{len(corpus)}] {contract.contract_key}: "
            f"original={contract.original_pdf.name} compare={contract.compare_pdf.name} HASH/PAGES OK"
        )
    print(
        "GOLDEN DATA: PASS "
        f"baseline={manifest['baseline_version']} "
        f"contracts={stats['contract_count']} cases={stats['expected_case_count']} "
        f"visual_positive={stats['visual_positive']} text_positive={stats['text_positive']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
