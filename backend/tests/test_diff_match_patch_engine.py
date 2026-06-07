from __future__ import annotations

import logging

from app.models import Clause, ClausePair
from app.services.diff import range_refiner
from app.services.diff_engine import DiffEngine


class FakeDiffMatchPatch:
    def __init__(self) -> None:
        self.Diff_Timeout = 0

    def diff_main(self, left: str, right: str) -> list[tuple[int, str]]:
        assert left == "合同金额为100万元"
        assert right == "合同金额为120万元"
        return [(0, "合同金额为1"), (-1, "0"), (1, "2"), (0, "0万元")]

    def diff_cleanupSemantic(self, diffs: list[tuple[int, str]]) -> None:
        return None


class FailingDiffMatchPatch:
    def diff_main(self, left: str, right: str) -> list[tuple[int, str]]:
        raise RuntimeError("simulated diff engine failure")

    def diff_cleanupSemantic(self, diffs: list[tuple[int, str]]) -> None:
        return None


def test_diff_match_patch_engine_maps_changes_back_to_text_ranges(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", FakeDiffMatchPatch)

    diff = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text="合同金额为100万元", normalized_text="合同金额为100万元"),
                compare=Clause(clause_id="N001", text="合同金额为120万元", normalized_text="合同金额为120万元"),
            )
        ]
    )[0]

    assert diff.diff_type == "MODIFY"
    assert [diff.original_text[item.start:item.end] for item in diff.original_change_ranges] == ["100"]
    assert [diff.compare_text[item.start:item.end] for item in diff.compare_change_ranges] == ["120"]
    assert [item.highlight_type for item in diff.original_change_ranges] == ["MODIFY"]
    assert [item.highlight_type for item in diff.compare_change_ranges] == ["MODIFY"]


def test_diff_match_patch_engine_falls_back_to_difflib_on_failure(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", FailingDiffMatchPatch)

    original_snippet, compare_snippet, original_ranges, compare_ranges = range_refiner.changed_snippets(
        "付款期限30天",
        "付款期限45天",
    )

    assert original_snippet == "30"
    assert compare_snippet == "45"
    assert original_ranges
    assert compare_ranges


def test_diff_match_patch_fallback_is_logged(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", FailingDiffMatchPatch)

    with caplog.at_level(logging.WARNING, logger="app.services.diff.range_refiner"):
        range_refiner.changed_snippets("付款期限30天", "付款期限45天")

    assert "falling back to difflib" in caplog.text


def test_difflib_engine_config_skips_diff_match_patch(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "difflib")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", FailingDiffMatchPatch)

    original_snippet, compare_snippet, original_ranges, compare_ranges = range_refiner.changed_snippets(
        "交付日期2026年6月1日",
        "交付日期2026年7月1日",
    )

    assert original_snippet == "6"
    assert compare_snippet == "7"
    assert original_ranges
    assert compare_ranges


def test_invalid_diff_engine_env_falls_back_with_log(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "unknown")

    with caplog.at_level(logging.WARNING, logger="app.services.diff.range_refiner"):
        assert range_refiner.configured_diff_engine() == "diff_match_patch"

    assert "Invalid DIFF_ENGINE" in caplog.text


def test_diff_engine_ignores_whitespace_only_changes(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", None)

    original_snippet, compare_snippet, original_ranges, compare_ranges = range_refiner.changed_snippets(
        "付款期限 30 天",
        "付款期限\n30 天",
    )

    assert original_snippet == ""
    assert compare_snippet == ""
    assert original_ranges == []
    assert compare_ranges == []
