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


def test_diff_builder_marks_critical_amount_field_change() -> None:
    diff = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text="合同金额为100万元", normalized_text="合同金额为100万元"),
                compare=Clause(clause_id="N001", text="合同金额为120万元", normalized_text="合同金额为120万元"),
            )
        ]
    )[0]

    assert "CRITICAL_FIELD_CHANGE" in diff.review_flags
    assert "CRITICAL_FIELD_AMOUNT_CHANGE" in diff.review_flags
    assert diff.match_score_details["critical_field_diff_types"] == ["AMOUNT"]
    assert diff.match_score_details["critical_field_guard_applied"] == 1.0
    assert [diff.original_text[item.start:item.end] for item in diff.original_change_ranges] == ["100"]
    assert [diff.compare_text[item.start:item.end] for item in diff.compare_change_ranges] == ["120"]


def test_diff_builder_marks_critical_date_field_change() -> None:
    diff = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(
                    clause_id="O001",
                    text="甲方应在2026年6月30日前付款。",
                    normalized_text="甲方应在2026年6月30日前付款。",
                ),
                compare=Clause(
                    clause_id="N001",
                    text="甲方应在2027年7月31日前付款。",
                    normalized_text="甲方应在2027年7月31日前付款。",
                ),
            )
        ]
    )[0]

    assert "CRITICAL_FIELD_CHANGE" in diff.review_flags
    assert "CRITICAL_FIELD_DATE_CHANGE" in diff.review_flags
    assert diff.match_score_details["critical_field_diff_types"] == ["DATE"]


def test_diff_builder_does_not_mark_punctuation_only_change_as_critical_field() -> None:
    diffs = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text="甲方应付款。", normalized_text="甲方应付款。"),
                compare=Clause(clause_id="N001", text="甲方应付款，", normalized_text="甲方应付款，"),
            )
        ]
    )

    assert diffs
    assert "CRITICAL_FIELD_CHANGE" not in diffs[0].review_flags
    assert "critical_field_diff_types" not in diffs[0].match_score_details


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


def test_percent_ocr_suffix_deletion_is_reported_as_modify(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", None)

    original_snippet, compare_snippet, original_ranges, compare_ranges = range_refiner.changed_snippets(
        "3%o作为违约金",
        "3%作为违约金",
    )

    assert original_snippet == "3%o"
    assert compare_snippet == "3%"
    assert [(item.start, item.end, item.highlight_type) for item in original_ranges] == [(0, 3, "MODIFY")]
    assert [(item.start, item.end, item.highlight_type) for item in compare_ranges] == [(0, 2, "MODIFY")]


def test_percent_ocr_suffix_punctuation_replacement_is_normalized_as_same_per_mille(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", None)

    original_snippet, compare_snippet, original_ranges, compare_ranges = range_refiner.changed_snippets(
        "3%o的违约金",
        "3%。的违约金",
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


def test_spatial_value_coverage_repairs_duplicate_value_false_delete(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", None)

    original = _clause_with_line_boxes(
        "O001",
        [
            ("22.5本合同一式两份,双方各持一份,传真件有效。", 60, 100),
            ("司】(盖章", 286, 336),
            ("账号:532658192135", 134, 353),
            ("行宁开发区支行", 173, 368),
            ("授权代表签字:", 286, 368),
            ("地址:南京市江宁经济技术开发区水阁路", 64, 404),
            ("电话:025-69833061", 64, 438),
            ("开户行:中行江宁开发区支行", 64, 455),
            ("账号:", 64, 475),
            ("账号:532658192135", 64, 475),
            ("银行行号:104301004820", 64, 492),
            ("乙方:【国能日新科技股份有限公司】(盖章)", 286, 336),
        ],
    )
    compare = _clause_with_line_boxes(
        "N001",
        [
            ("22.5本合同一式两份,双方各持一份,传真件有效。", 60, 100),
            ("甲方:【南", 67, 327),
            ("司】(盖章", 67, 350),
            ("地址:南京江江州汉区小同路", 67, 394),
            ("电话:025-69833061", 67, 430),
            ("开户行:中行江宁开发区支行", 67, 446),
            ("账号:532658192135", 67, 463),
            ("账号:", 190, 463),
            ("银行行号:104301004820", 67, 480),
            ("2021.4.17", 338, 486),
            ("盖章)", 496, 325),
            ("刘万社", 501, 350),
        ],
    )

    diff = DiffEngine().build_diffs([ClausePair(original=original, compare=compare)])[0]

    deleted_texts = [diff.original_text[item.start:item.end] for item in diff.original_change_ranges]
    assert "账号:532658192135" not in deleted_texts
    assert "SPATIAL_VALUE_COVERAGE_REPAIRED" in diff.review_flags


def test_spatial_value_coverage_keeps_value_delete_without_visual_match(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", None)

    original = _clause_with_line_boxes(
        "O001",
        [
            ("确认编号:A100", 60, 100),
            ("确认编号:A100", 60, 180),
        ],
    )
    compare = _clause_with_line_boxes(
        "N001",
        [
            ("确认编号:A100", 60, 100),
        ],
    )

    diff = DiffEngine().build_diffs([ClausePair(original=original, compare=compare)])[0]

    deleted_texts = [diff.original_text[item.start:item.end] for item in diff.original_change_ranges]
    assert "确认编号:A100" in deleted_texts
    assert "SPATIAL_VALUE_COVERAGE_REPAIRED" not in diff.review_flags


def test_cross_column_prefix_fragment_expands_to_deleted_source_line(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", None)

    original = _clause_with_line_boxes(
        "O001",
        [
            ("22.5本合同一式两份,双方各持一份,传真件有效。", 60, 100),
            ("授权代表签字:", 286, 368),
            ("纳税人识别号:9132011", 64, 386),
            ("纳税人识别号:", 286, 386),
        ],
    )
    compare = _clause_with_line_boxes(
        "N001",
        [
            ("22.5本合同一式两份,双方各持一份,传真件有效。", 60, 100),
            ("授权代表签", 68, 368),
            ("纳税人识别", 68, 386),
            ("地址:南京江江州汉区小同路", 68, 404),
        ],
    )

    diff = DiffEngine().build_diffs([ClausePair(original=original, compare=compare)])[0]

    deleted_texts = [diff.original_text[item.start:item.end] for item in diff.original_change_ranges]
    assert "授权代表签字:" in deleted_texts
    assert "纳税人识别号:" in deleted_texts
    assert "字:" not in deleted_texts
    assert "号:" not in deleted_texts
    assert "SPATIAL_CROSS_COLUMN_PREFIX_REPAIRED" in diff.review_flags


def test_cross_column_prefix_fragment_keeps_same_column_partial_ocr(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", None)

    original = _clause_with_line_boxes(
        "O001",
        [
            ("授权代表签字:", 68, 368),
            ("纳税人识别号:", 68, 386),
        ],
    )
    compare = _clause_with_line_boxes(
        "N001",
        [
            ("授权代表签", 68, 368),
            ("纳税人识别", 68, 386),
        ],
    )

    diff = DiffEngine().build_diffs([ClausePair(original=original, compare=compare)])[0]

    deleted_texts = [diff.original_text[item.start:item.end] for item in diff.original_change_ranges]
    assert "字:" in deleted_texts
    assert "号:" in deleted_texts
    assert "授权代表签字:" not in deleted_texts
    assert "纳税人识别号:" not in deleted_texts
    assert "SPATIAL_CROSS_COLUMN_PREFIX_REPAIRED" not in diff.review_flags


def test_spatial_substring_coverage_removes_shared_suffix_from_large_delete(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", None)

    original = _clause_with_line_boxes(
        "O001",
        [
            ("22.5本合同一式两份,双方各持一份,传真件有效。", 60, 100),
            ("乙方:【国能日新科技股份有限公司】(盖章)", 286, 336),
        ],
    )
    compare = _clause_with_line_boxes(
        "N001",
        [
            ("22.5本合同一式两份,双方各持一份,传真件有效。", 60, 100),
            ("盖章)", 456, 336),
        ],
    )

    diff = DiffEngine().build_diffs([ClausePair(original=original, compare=compare)])[0]

    deleted_texts = [diff.original_text[item.start:item.end] for item in diff.original_change_ranges]
    added_texts = [diff.compare_text[item.start:item.end] for item in diff.compare_change_ranges]
    assert "盖章)" not in "".join(deleted_texts)
    assert "盖章)" not in added_texts
    assert "乙方:【国能日新科技股份有限公司】(" in deleted_texts
    assert "SPATIAL_SUBSTRING_COVERAGE_REPAIRED" in diff.review_flags


def test_spatial_substring_coverage_keeps_shared_suffix_on_different_row(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_ENGINE", "diff_match_patch")
    monkeypatch.setattr(range_refiner, "_DiffMatchPatch", None)

    original = _clause_with_line_boxes(
        "O001",
        [
            ("22.5本合同一式两份,双方各持一份,传真件有效。", 60, 100),
            ("乙方:【国能日新科技股份有限公司】(盖章)", 286, 336),
        ],
    )
    compare = _clause_with_line_boxes(
        "N001",
        [
            ("22.5本合同一式两份,双方各持一份,传真件有效。", 60, 100),
            ("盖章)", 456, 420),
        ],
    )

    diff = DiffEngine().build_diffs([ClausePair(original=original, compare=compare)])[0]

    deleted_texts = [diff.original_text[item.start:item.end] for item in diff.original_change_ranges]
    added_texts = [diff.compare_text[item.start:item.end] for item in diff.compare_change_ranges]
    assert "乙方:【国能日新科技股份有限公司】(盖章)" in deleted_texts
    assert "盖章)" in added_texts
    assert "SPATIAL_SUBSTRING_COVERAGE_REPAIRED" not in diff.review_flags


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
