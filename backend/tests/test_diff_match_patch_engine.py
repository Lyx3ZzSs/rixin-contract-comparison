from __future__ import annotations

import logging

from app.models import BBox, CharBox, Clause, ClausePair
from app.services.evidence_locator import EvidenceLocator
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


def test_spatial_line_pairing_repairs_two_column_form_label_false_add(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", None)

    original = _clause_with_line_boxes(
        "O001",
        [
            ("法人代表或授权委托人:", 80, 100),
            ("法人代表或授权委托人:", 320, 100),
        ],
    )
    compare = _clause_with_line_boxes(
        "N001",
        [
            ("法人", 80, 100),
            ("法人代表或", 320, 100),
        ],
    )

    diff = DiffEngine().build_diffs([ClausePair(original=original, compare=compare)])[0]

    assert [diff.original_text[item.start:item.end] for item in diff.original_change_ranges] == [
        "代表或授权委托人:",
        "授权委托人:",
    ]
    assert diff.compare_change_ranges == []
    assert "SPATIAL_LINE_PAIRING_REPAIRED" in diff.review_flags

    [located] = EvidenceLocator().locate([diff], [original], [compare])
    assert [item.text for item in located.original_evidence] == ["代表或授权委托人:", "授权委托人:"]
    assert located.compare_evidence == []
    assert located.original_evidence[0].bbox.x0 > original.char_boxes[0].bbox.x0


def _clause_with_line_boxes(clause_id: str, lines: list[tuple[str, float, float]]) -> Clause:
    text = "\n".join(line for line, _, _ in lines)
    char_boxes: list[CharBox | None] = []
    text_index = 0
    for line_index, (line, x0, y0) in enumerate(lines):
        for offset, char in enumerate(line):
            char_boxes.append(
                CharBox(
                    char=char,
                    page_no=1,
                    bbox=BBox(
                        x0=x0 + offset * 10,
                        y0=y0,
                        x1=x0 + offset * 10 + 8,
                        y1=y0 + 14,
                    ),
                    text_index=text_index,
                )
            )
            text_index += 1
        if line_index < len(lines) - 1:
            char_boxes.append(None)
            text_index += 1
    return Clause(
        clause_id=clause_id,
        text=text,
        normalized_text=text,
        char_boxes=char_boxes,
    )
