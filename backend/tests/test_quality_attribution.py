from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_quality_attribution import analyze_run_dir, write_attribution_report


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_quality_report(run_dir: Path) -> None:
    _write_json(
        run_dir / "quality.json",
        {
            "case_count": 1,
            "aggregate": {
                "false_positive_count": 1,
                "false_negative_count": 0,
                "precision": 0.5,
                "recall": 1.0,
                "evidence_hit_rate": 1.0,
            },
            "cases": [
                {
                    "case_id": "case_a",
                    "status": "PASSED",
                    "false_positive_count": 1,
                    "false_negative_count": 0,
                    "unexpected_actual_diffs": [{"diff_id": "D_UNEXPECTED"}],
                    "missed_expected_diffs": [],
                    "annotation_summary": {"APPROVED": 1, "DRAFT": 0, "REJECTED": 0},
                }
            ],
        },
    )


def test_analyze_run_dir_combines_quality_and_alignment_debug_artifacts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(
        run_dir / "debug" / "case_a" / "match_matrix_summary.json",
        {
            "method_counts": {"same_clause_key_weighted": 1},
            "low_confidence_alignment_count": 1,
            "alignment_risk_flag_counts": {
                "CRITICAL_TOKEN_MISMATCH": 1,
                "POSSIBLE_CLAUSE_MISALIGNMENT": 1,
            },
        },
    )
    _write_json(
        run_dir / "debug" / "case_a" / "clause_matches.json",
        [
            {
                "original_clause_id": "O001",
                "compare_clause_id": "N001",
                "match_method": "same_clause_key_weighted",
                "match_confidence": "LOW",
                "score_details": {
                    "body_length_coverage": 0.42,
                    "alignment": {
                        "body_similarity": 0.44,
                        "critical_token_overlap": 0.0,
                        "risk_flags": [
                            "CRITICAL_TOKEN_MISMATCH",
                            "POSSIBLE_CLAUSE_MISALIGNMENT",
                        ],
                    },
                },
            }
        ],
    )

    report = analyze_run_dir(run_dir)

    assert report["case_count"] == 1
    assert report["aggregate"]["false_positive_count"] == 1
    assert report["aggregate"]["low_confidence_alignment_count"] == 1
    assert report["aggregate"]["alignment_risk_flag_counts"] == {
        "CRITICAL_TOKEN_MISMATCH": 1,
        "POSSIBLE_CLAUSE_MISALIGNMENT": 1,
    }
    assert report["aggregate"]["match_method_counts"] == {"same_clause_key_weighted": 1}
    assert report["aggregate"]["attribution_counts"]["LOW_CONFIDENCE_ALIGNMENT"] == 1
    assert report["aggregate"]["attribution_counts"]["KEY_TOKEN_CONFLICT"] == 1
    assert report["aggregate"]["attribution_counts"]["SAME_KEY_LOW_BODY_COVERAGE"] == 1
    assert report["aggregate"]["attribution_counts"]["UNEXPECTED_ACTUAL_NEEDS_LABEL"] == 1
    case = report["cases"][0]
    assert case["case_id"] == "case_a"
    assert case["quality_status"] == "PASSED"
    assert case["false_positive_count"] == 1
    assert case["false_negative_count"] == 0
    assert case["low_confidence_alignment_count"] == 1
    assert case["alignment_risk_flag_counts"]["CRITICAL_TOKEN_MISMATCH"] == 1
    assert case["suspicious_matches"][0]["original_clause_id"] == "O001"
    assert case["suspicious_matches"][0]["risk_flags"] == [
        "CRITICAL_TOKEN_MISMATCH",
        "POSSIBLE_CLAUSE_MISALIGNMENT",
    ]
    assert "LOW_CONFIDENCE_ALIGNMENT" in case["attribution_tags"]
    assert case["warnings"] == []


def test_analyze_run_dir_maps_risk_methods_and_recall_attribution_tags(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_json(
        run_dir / "quality.json",
        {
            "case_count": 1,
            "aggregate": {
                "false_positive_count": 0,
                "false_negative_count": 1,
            },
            "cases": [
                {
                    "case_id": "case_b",
                    "status": "FAILED",
                    "false_positive_count": 0,
                    "false_negative_count": 1,
                    "unexpected_actual_diffs": [],
                    "missed_expected_diffs": [{"diff_id": "D_MISSED"}],
                    "annotation_summary": {"APPROVED": 1, "DRAFT": 2},
                }
            ],
        },
    )
    _write_json(
        run_dir / "debug" / "case_b" / "match_matrix_summary.json",
        {
            "method_counts": {
                "body_weighted_similarity": 1,
                "section_mismatch_blocked": 1,
            },
            "low_confidence_alignment_count": 1,
            "alignment_risk_flag_counts": {
                "CRITICAL_TOKEN_MISMATCH": 1,
                "TEXT_MATCH_NUMBER_MISMATCH": 1,
                "TITLE_MATCH_TEXT_MISMATCH": 1,
            },
        },
    )
    _write_json(
        run_dir / "debug" / "case_b" / "clause_matches.json",
        [
            {
                "original_clause_id": "O010",
                "compare_clause_id": "N010",
                "match_method": "body_weighted_similarity",
                "match_confidence": "MEDIUM",
                "score_details": {
                    "alignment": {
                        "risk_flags": ["TEXT_MATCH_NUMBER_MISMATCH"],
                    },
                },
            },
            {
                "original_clause_id": "O011",
                "compare_clause_id": "N011",
                "match_method": "section_mismatch_blocked",
                "match_confidence": "MEDIUM",
                "score_details": {
                    "alignment": {
                        "risk_flags": ["TITLE_MATCH_TEXT_MISMATCH"],
                    },
                },
            },
        ],
    )

    report = analyze_run_dir(run_dir)

    tags = set(report["cases"][0]["attribution_tags"])
    assert {
        "CRITICAL_TOKEN_MISMATCH",
        "TEXT_MATCH_NUMBER_MISMATCH",
        "TITLE_MATCH_TEXT_MISMATCH",
        "POSSIBLE_CLAUSE_MISALIGNMENT",
        "BODY_ONLY_MATCH",
        "SECTION_MISMATCH_CANDIDATE",
        "SUSPICIOUS_MATCH_METHOD",
        "POSSIBLE_FIELD_DIFF_SUPPRESSED",
        "EXPECTED_DIFF_TOO_SPARSE",
        "KEY_TOKEN_RECALL_RISK",
        "GOLD_CASE_NEEDS_REVIEW",
    }.issubset(tags)
    assert report["aggregate"]["attribution_counts"]["SUSPICIOUS_MATCH_METHOD"] == 1
    assert report["aggregate"]["attribution_counts"]["BODY_ONLY_MATCH"] == 1
    assert report["aggregate"]["attribution_counts"]["SECTION_MISMATCH_CANDIDATE"] == 1


def test_analyze_run_dir_records_warnings_for_missing_debug_artifacts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)

    report = analyze_run_dir(run_dir)

    case = report["cases"][0]
    assert "case_a: missing debug artifact match_matrix_summary.json" in case["warnings"]
    assert "case_a: missing debug artifact clause_matches.json" in case["warnings"]
    assert case["low_confidence_alignment_count"] == 0
    assert case["alignment_risk_flag_counts"] == {}


def test_analyze_run_dir_records_warnings_for_malformed_top_level_debug_artifacts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(run_dir / "debug" / "case_a" / "match_matrix_summary.json", ["not-object"])
    _write_json(run_dir / "debug" / "case_a" / "clause_matches.json", {"not": "array"})

    report = analyze_run_dir(run_dir)

    warnings = report["cases"][0]["warnings"]
    assert any("invalid match_matrix_summary.json" in warning for warning in warnings)
    assert any("invalid clause_matches.json" in warning for warning in warnings)
    assert report["cases"][0]["alignment_risk_flag_counts"] == {}
    assert report["cases"][0]["suspicious_matches"] == []


def test_analyze_run_dir_records_warnings_for_non_object_clause_match_items(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(run_dir / "debug" / "case_a" / "match_matrix_summary.json", {})
    _write_json(run_dir / "debug" / "case_a" / "clause_matches.json", ["bad-item"])

    report = analyze_run_dir(run_dir)

    assert any(
        "invalid clause_matches[0]" in warning
        for warning in report["cases"][0]["warnings"]
    )


def test_analyze_run_dir_records_warnings_for_malformed_clause_match_shapes(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(run_dir / "debug" / "case_a" / "match_matrix_summary.json", {})
    _write_json(
        run_dir / "debug" / "case_a" / "clause_matches.json",
        [
            {
                "match_method": "body_weighted_similarity",
                "score_details": "not-a-dict",
            },
            {
                "match_method": "same_clause_key_weighted",
                "score_details": {"alignment": "not-a-dict"},
            },
            {
                "match_method": "partial_body_similarity",
                "score_details": {"alignment": {"risk_flags": "not-a-list"}},
            },
        ],
    )

    report = analyze_run_dir(run_dir)

    warnings = report["cases"][0]["warnings"]
    assert any("invalid score_details" in warning for warning in warnings)
    assert any("invalid alignment" in warning for warning in warnings)
    assert any("invalid risk_flags" in warning for warning in warnings)
    assert report["cases"][0]["match_method_counts"] == {
        "body_weighted_similarity": 1,
        "same_clause_key_weighted": 1,
        "partial_body_similarity": 1,
    }


def test_analyze_run_dir_ignores_malformed_alignment_shapes(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(
        run_dir / "debug" / "case_a" / "match_matrix_summary.json",
        {
            "method_counts": {"body_weighted_similarity": 1},
            "alignment_risk_flag_counts": "not-a-dict",
            "low_confidence_alignment_count": "not-a-number",
        },
    )
    _write_json(
        run_dir / "debug" / "case_a" / "clause_matches.json",
        [
            {
                "match_method": "body_weighted_similarity",
                "score_details": {"alignment": "bad"},
            },
            {
                "match_method": "same_clause_key_weighted",
                "score_details": {"alignment": {"risk_flags": "bad"}},
            },
        ],
    )

    report = analyze_run_dir(run_dir)

    case = report["cases"][0]
    assert case["alignment_risk_flag_counts"] == {}
    assert case["match_method_counts"] == {"body_weighted_similarity": 1}
    assert "SUSPICIOUS_MATCH_METHOD" in case["attribution_tags"]
    assert any("invalid alignment" in warning for warning in case["warnings"])
    assert any("invalid risk_flags" in warning for warning in case["warnings"])


def test_analyze_run_dir_ignores_bool_debug_counts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(
        run_dir / "debug" / "case_a" / "match_matrix_summary.json",
        {
            "method_counts": {
                "body_weighted_similarity": True,
                "same_clause_key_weighted": 1,
            },
            "alignment_risk_flag_counts": {
                "CRITICAL_TOKEN_MISMATCH": True,
                "TEXT_MATCH_NUMBER_MISMATCH": 1,
            },
            "low_confidence_alignment_count": True,
        },
    )
    _write_json(run_dir / "debug" / "case_a" / "clause_matches.json", [])

    report = analyze_run_dir(run_dir)

    case = report["cases"][0]
    assert case["match_method_counts"] == {"same_clause_key_weighted": 1}
    assert case["alignment_risk_flag_counts"] == {"TEXT_MATCH_NUMBER_MISMATCH": 1}
    assert case["low_confidence_alignment_count"] == 0


def test_analyze_run_dir_counts_matcher_risk_flags(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(run_dir / "debug" / "case_a" / "match_matrix_summary.json", {})
    _write_json(
        run_dir / "debug" / "case_a" / "clause_matches.json",
        [
            {
                "original_clause_id": "O001",
                "compare_clause_id": "N001",
                "match_method": "same_clause_key_weighted",
                "match_confidence": "LOW",
                "score_details": {
                    "matcher_risk_flags": [
                        "SAME_KEY_LOW_BODY_COVERAGE",
                        "CRITICAL_TOKEN_CONFLICT",
                    ],
                    "matcher_guard_applied": 1.0,
                    "body_length_coverage": 0.42,
                    "alignment": {
                        "body_similarity": 0.44,
                        "critical_token_overlap": 0.0,
                        "risk_flags": ["CRITICAL_TOKEN_MISMATCH"],
                    },
                },
            },
            {
                "original_clause_id": "O002",
                "compare_clause_id": "N002",
                "match_method": "body_weighted_similarity",
                "match_confidence": "LOW",
                "score_details": {
                    "matcher_risk_flags": ["BODY_ONLY_ALIGNMENT_RISK"],
                    "matcher_guard_applied": 1.0,
                    "alignment": {"risk_flags": ["TEXT_MATCH_NUMBER_MISMATCH"]},
                },
            },
        ],
    )

    report = analyze_run_dir(run_dir)

    case = report["cases"][0]
    assert case["matcher_risk_flag_counts"] == {
        "SAME_KEY_LOW_BODY_COVERAGE": 1,
        "CRITICAL_TOKEN_CONFLICT": 1,
        "BODY_ONLY_ALIGNMENT_RISK": 1,
    }
    assert report["aggregate"]["matcher_risk_flag_counts"] == case[
        "matcher_risk_flag_counts"
    ]
    assert case["suspicious_matches"][0]["matcher_risk_flags"] == [
        "SAME_KEY_LOW_BODY_COVERAGE",
        "CRITICAL_TOKEN_CONFLICT",
    ]
    assert "SAME_KEY_LOW_BODY_COVERAGE" in case["attribution_tags"]
    assert "CRITICAL_TOKEN_CONFLICT" in case["attribution_tags"]
    assert "BODY_ONLY_ALIGNMENT_RISK" in case["attribution_tags"]


def test_write_attribution_report_writes_quality_attribution_json(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    path = write_attribution_report(run_dir, {"run_id": "run", "cases": []})

    assert path == run_dir / "quality_attribution.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "run_id": "run",
        "cases": [],
    }


def test_main_writes_report_and_prints_summary(tmp_path: Path, capsys) -> None:
    from scripts import analyze_quality_attribution

    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)

    exit_code = analyze_quality_attribution.main(["--run-dir", str(run_dir)])

    assert exit_code == 0
    payload = json.loads((run_dir / "quality_attribution.json").read_text(encoding="utf-8"))
    assert payload["case_count"] == 1
    captured = json.loads(capsys.readouterr().out)
    assert captured["output_path"] == str(run_dir / "quality_attribution.json")
    assert captured["case_count"] == 1


def test_main_returns_error_for_missing_run_dir(tmp_path: Path, capsys) -> None:
    from scripts import analyze_quality_attribution

    exit_code = analyze_quality_attribution.main(["--run-dir", str(tmp_path / "missing")])

    assert exit_code == 1
    captured = json.loads(capsys.readouterr().out)
    assert "run directory does not exist" in captured["error"]
