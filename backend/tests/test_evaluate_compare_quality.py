import json
from pathlib import Path

from scripts.evaluate_compare_quality import evaluate_case_root


def test_evaluate_compare_quality_fixture(tmp_path: Path) -> None:
    case_dir = tmp_path / "simple_text"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "expected_diffs": [
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "付款",
                        "original_contains": "30 days",
                        "compare_contains": "45 days",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_SIMPLE_TEXT",
                "status": "COMPLETED",
                "stage": "已完成",
                "progress_percent": 100,
                "diff_count": 1,
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "title": "付款",
                        "original_text": "Buyer shall pay within 30 days.",
                        "compare_text": "Buyer shall pay within 45 days.",
                        "source_type": "clause",
                        "original_evidence": [
                            {
                                "page_no": 1,
                                "bbox": {"x0": 72, "y0": 120, "x1": 240, "y1": 146},
                                "method": "char_exact",
                                "confidence": 0.98,
                                "evidence_quality": "HIGH",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = evaluate_case_root(tmp_path)

    assert report["case_count"] == 1
    assert report["aggregate"]["true_positive_count"] == 1
    assert report["aggregate"]["false_positive_count"] == 0
    assert report["aggregate"]["false_negative_count"] == 0
    assert report["aggregate"]["precision"] == 1.0
    assert report["aggregate"]["recall"] == 1.0
    assert report["aggregate"]["evidence_hit_rate"] == 1.0
