import json
from pathlib import Path

from scripts.evaluate_layout_quality import evaluate_case_root, write_html_report


def _write_basic_layout_case(case_root: Path, case_id: str = "basic") -> None:
    case_dir = case_root / case_id
    case_dir.mkdir(parents=True)
    payload = {
        "pages": [
            {
                "page_no": 1,
                "blocks": [
                    {
                        "block_role": "paragraph_title",
                        "bbox": {"x0": 40, "y0": 20, "x1": 560, "y1": 60},
                        "reading_order": 1,
                    },
                    {
                        "block_role": "text",
                        "bbox": {"x0": 40, "y0": 100, "x1": 260, "y1": 130},
                        "reading_order": 2,
                    },
                    {
                        "block_role": "text",
                        "bbox": {"x0": 340, "y0": 100, "x1": 560, "y1": 130},
                        "reading_order": 3,
                    },
                ],
            }
        ]
    }
    (case_dir / "expected.json").write_text(json.dumps(payload), encoding="utf-8")
    (case_dir / "actual.json").write_text(json.dumps(payload), encoding="utf-8")


def test_evaluate_layout_quality_fixture(tmp_path: Path) -> None:
    _write_basic_layout_case(tmp_path)

    report = evaluate_case_root(tmp_path)

    assert report["case_count"] == 1
    assert report["aggregate"]["region_precision"] == 1.0
    assert report["aggregate"]["region_recall"] == 1.0
    assert report["aggregate"]["bbox_hit_rate"] == 1.0
    assert report["aggregate"]["reading_order_accuracy"] == 1.0
    assert report["aggregate"]["reading_order_pair_accuracy"] == 1.0
    assert report["aggregate"]["label_macro_f1"] == 1.0
    assert report["aggregate"]["invalid_bbox_count"] == 0
    assert report["threshold_failures"] == []


def test_evaluate_layout_quality_writes_html_report(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    _write_basic_layout_case(case_root)

    report = evaluate_case_root(case_root)

    write_html_report(tmp_path, report)

    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "basic.html").exists()


def test_approved_real_layout_cases_pass_thresholds(tmp_path: Path) -> None:
    case_root = tmp_path / "layout_real_cases"
    for index in range(8):
        _write_basic_layout_case(case_root, f"case_{index}")

    report = evaluate_case_root(case_root)

    assert report["case_count"] == 8
    assert report["threshold_failures"] == []
    assert all(case["issues"] == [] for case in report["cases"])
