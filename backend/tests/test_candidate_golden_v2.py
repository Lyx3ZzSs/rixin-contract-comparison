from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ORACLE = Path(__file__).parent / "fixtures" / "candidate_golden_v2" / "oracle.json"


def test_text_recall_requires_visible_obligation_and_correct_evidence_page() -> None:
    from scripts.replay_candidate_golden_v2 import _match_expected_text

    case = {"expected_compare_fragments": ["30日", "最终验收"], "expected_pages": [3]}
    diff = {
        "diff_type": "ADD",
        "compare_text": "30日内完成并通过最终验收",
        "compare_snippet": "2.3",
        "compare_evidence": [{"page_no": 3}],
    }
    assert _match_expected_text(case, [diff], set()) is None
    diff["compare_snippet"] = diff["compare_text"]
    assert _match_expected_text(case, [diff], set()) is not None
    diff["compare_evidence"] = [{"page_no": 1}]
    assert _match_expected_text(case, [diff], set()) is None


def test_candidate_golden_v2_is_non_blocking_and_has_reviewed_case_counts() -> None:
    payload = json.loads(ORACLE.read_text(encoding="utf-8"))
    cases = payload["cases"]
    counts = Counter(item["classification"] for item in cases)

    assert payload["oracle_version"] == "candidate-golden-v2"
    assert payload["blocking"] is False
    assert payload["baseline"] == {
        "diffs": 103,
        "true_positive": 34,
        "false_positive": 69,
        "false_negative": 12,
        "review": 0,
    }
    assert counts == {
        "TRUE_POSITIVE": 34,
        "FALSE_POSITIVE": 69,
        "FALSE_NEGATIVE": 12,
    }
    case_ids = [item["case_id"] for item in cases]
    assert len(case_ids) == len(set(case_ids)) == 115
    assert all(not case_id.startswith("D") for case_id in case_ids)
    assert all(item["semantic_fact"].strip() for item in cases)
    phase3_targets = [item for item in cases if item.get("phase3_target")]
    assert len(phase3_targets) == 9
    assert all(item.get("expected_object_type") for item in phase3_targets)
    assert all(item.get("expected_party_role") for item in phase3_targets)
    assert all(item.get("expected_pages") for item in phase3_targets)
