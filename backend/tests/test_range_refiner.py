from __future__ import annotations

from app.models import TextRange
from app.services.diff.range_refiner import (
    changed_snippets,
    refine_changed_ranges,
    refine_inline_changed_ranges,
)


class TestInsertWithinCompositeHunk:
    """Regression: insert within a composite hunk must be classified as ADD.

    Root cause: D018 had "2021.4.17" (pure insert) classified as MODIFY
    because ``refine_changed_ranges`` fell through to ``coarse_modify_ranges``
    when ``should_refine_inline`` returned False.
    """

    def test_insert_classified_as_add(self) -> None:
        left = "日期:"
        right = "银行行号:\n2021.4.17"

        left_ranges, right_ranges = refine_changed_ranges(
            left, 0, len(left), right, 0, len(right),
        )

        right_add_texts = [right[r.start:r.end] for r in right_ranges if r.highlight_type == "ADD"]
        assert any("2021.4.17" in t for t in right_add_texts), (
            f"Expected ADD covering '2021.4.17', got: {right_ranges}"
        )

    def test_replace_portion_still_modify(self) -> None:
        left = "日期:"
        right = "银行行号:\n2021.4.17"

        left_ranges, right_ranges = refine_changed_ranges(
            left, 0, len(left), right, 0, len(right),
        )

        right_modify_texts = [right[r.start:r.end] for r in right_ranges if r.highlight_type == "MODIFY"]
        assert any("银行行号" in t for t in right_modify_texts), (
            f"Expected MODIFY covering '银行行号', got: {right_ranges}"
        )


class TestCompositeHunkMixedReplaceInsert:
    """General case: a hunk containing both replace and insert opcodes."""

    def test_mixed_ops_produce_modify_and_add(self) -> None:
        # ":" is shared so SequenceMatcher produces replace + equal + insert
        left = "abc:"
        right = "xyz:\nNEW"

        left_ranges, right_ranges = refine_inline_changed_ranges(
            left, 0, len(left), right, 0, len(right),
        )

        types = {r.highlight_type for r in right_ranges}
        assert "ADD" in types, f"Expected ADD in right ranges, got: {right_ranges}"

    def test_no_shared_anchor_is_single_modify(self) -> None:
        # No shared text → single replace → MODIFY on both sides
        left = "abc"
        right = "xyz\nNEW"

        left_ranges, right_ranges = refine_inline_changed_ranges(
            left, 0, len(left), right, 0, len(right),
        )

        assert all(r.highlight_type == "MODIFY" for r in right_ranges)


class TestOneSideSingleLine:
    """When one side has 1 line and the other has multiple lines,
    refinement should not degenerate to coarse MODIFY."""

    def test_single_vs_multi_line_produces_sub_ranges(self) -> None:
        left = "日期:"
        right = "银行行号:\n2021.4.17"

        _, right_ranges = refine_changed_ranges(
            left, 0, len(left), right, 0, len(right),
        )

        assert len(right_ranges) >= 2, (
            f"Expected multiple sub-ranges, got: {right_ranges}"
        )


class TestNoSharedCjkStillRefines:
    """When texts share no CJK sequence, refinement should still proceed."""

    def test_no_shared_cjk_does_not_skip(self) -> None:
        left = "日期:"
        right = "银行行号:2021.4.17"

        _, right_ranges = refine_changed_ranges(
            left, 0, len(left), right, 0, len(right),
        )

        types = {r.highlight_type for r in right_ranges}
        assert "MODIFY" in types or "ADD" in types, (
            f"Expected MODIFY or ADD, got: {right_ranges}"
        )


class TestD018EndToEnd:
    """End-to-end test using D018's full clause texts."""

    ORIGINAL = (
        "39号\n电话:\n电话:025-69833061\n开户行:\n开户行:中行江宁开发区支行\n"
        "账号:\n账号:532658192135\n银行行号:\n银行行号:104301004820\n"
        "日期:\n日期:\n2026.4.17"
    )
    COMPARE = (
        "39号\n电话:025-69833061\n电话:\n开户行:中行江宁开发区支行\n开户行:\n"
        "账号:532658192135\n账号:\n银行行号:104301004820\n银行行号:\n"
        "2021.4.17\n日期:\n2026.4.17\n日期:"
    )

    def test_2021_date_is_add(self) -> None:
        _, _, _, right_ranges = changed_snippets(self.ORIGINAL, self.COMPARE)
        add_texts = [self.COMPARE[r.start:r.end] for r in right_ranges if r.highlight_type == "ADD"]
        assert any("2021.4.17" in t for t in add_texts), (
            f"'2021.4.17' should be ADD, got ranges: {right_ranges}"
        )

    def test_form_labels_not_flagged_as_modify(self) -> None:
        """Bare labels that exist identically in both texts should not be MODIFY/DELETE."""
        _, _, left_ranges, right_ranges = changed_snippets(self.ORIGINAL, self.COMPARE)
        left_modify_texts = [self.ORIGINAL[r.start:r.end] for r in left_ranges if r.highlight_type == "MODIFY"]
        left_delete_texts = [self.ORIGINAL[r.start:r.end] for r in left_ranges if r.highlight_type == "DELETE"]
        right_modify_texts = [self.COMPARE[r.start:r.end] for r in right_ranges if r.highlight_type == "MODIFY"]
        for label in ("电话", "开户行", "账号", "银行行号", "日期"):
            assert not any(label in t for t in left_modify_texts), (
                f"'{label}' should not be MODIFY on left, got: {left_modify_texts}"
            )
            assert not any(label in t for t in left_delete_texts), (
                f"'{label}' should not be DELETE on left, got: {left_delete_texts}"
            )
            assert not any(label in t for t in right_modify_texts), (
                f"'{label}' should not be MODIFY on right, got: {right_modify_texts}"
            )

    def test_no_false_left_ranges(self) -> None:
        """After fix, original side should have no ranges for D018."""
        _, _, left_ranges, _ = changed_snippets(self.ORIGINAL, self.COMPARE)
        assert left_ranges == [], (
            f"Expected no left ranges for D018, got: {left_ranges}"
        )


class TestBasicDiffSemantics:
    """Ensure line-first diffing preserves genuine changes."""

    def test_numeric_change_detected(self) -> None:
        left = "金额:1000元"
        right = "金额:2000元"
        _, _, left_ranges, right_ranges = changed_snippets(left, right)
        modify_texts = [left[r.start:r.end] for r in left_ranges if r.highlight_type == "MODIFY"]
        assert any("1000" in t for t in modify_texts), (
            f"'1000' should still be MODIFY, got: {left_ranges}"
        )

    def test_add_preserved(self) -> None:
        left = "第一条 适用范围"
        right = "第一条 适用范围\n第二条 定义"
        _, _, _, right_ranges = changed_snippets(left, right)
        add_texts = [right[r.start:r.end] for r in right_ranges if r.highlight_type == "ADD"]
        assert any("第二条" in t for t in add_texts), (
            f"'第二条' should be ADD, got: {right_ranges}"
        )

    def test_delete_preserved(self) -> None:
        left = "第一条 适用范围\n第二条 定义"
        right = "第一条 适用范围"
        _, _, left_ranges, _ = changed_snippets(left, right)
        delete_texts = [left[r.start:r.end] for r in left_ranges if r.highlight_type == "DELETE"]
        assert any("第二条" in t for t in delete_texts), (
            f"'第二条' should be DELETE, got: {left_ranges}"
        )

    def test_identical_texts_no_ranges(self) -> None:
        left = "电话:\n电话:025-69833061"
        right = "电话:\n电话:025-69833061"
        _, _, left_ranges, right_ranges = changed_snippets(left, right)
        assert left_ranges == [] and right_ranges == []


class TestLineFirstMatching:
    """Tests for the line-first diffing strategy."""

    def test_reordered_identical_lines_no_false_positives(self) -> None:
        left = "电话:025\n地址:南京\n日期:2024"
        right = "日期:2024\n电话:025\n地址:南京"
        _, _, left_ranges, right_ranges = changed_snippets(left, right)
        assert left_ranges == [], f"Expected no left ranges, got: {left_ranges}"
        assert right_ranges == [], f"Expected no right ranges, got: {right_ranges}"

    def test_reordered_lines_with_genuine_add(self) -> None:
        left = "电话:025\n地址:南京\n日期:2024"
        right = "地址:南京\n电话:025\n日期:2024\n新增内容"
        _, _, _, right_ranges = changed_snippets(left, right)
        add_texts = [right[r.start:r.end] for r in right_ranges if r.highlight_type == "ADD"]
        assert any("新增" in t for t in add_texts), (
            f"'新增内容' should be ADD, got: {right_ranges}"
        )

    def test_partially_matched_line(self) -> None:
        left = "金额:100元\n日期:2024"
        right = "金额:200元\n日期:2024"
        _, _, left_ranges, _ = changed_snippets(left, right)
        modify_texts = [left[r.start:r.end] for r in left_ranges if r.highlight_type == "MODIFY"]
        assert any("100" in t for t in modify_texts), (
            f"'100' should be MODIFY, got: {left_ranges}"
        )
        assert not any("日期" in t for t in modify_texts), (
            f"'日期' should not appear in MODIFY, got: {modify_texts}"
        )

    def test_single_line_falls_through_to_character_first(self) -> None:
        left = "金额:100元"
        right = "金额:200元"
        _, _, left_ranges, _ = changed_snippets(left, right)
        modify_texts = [left[r.start:r.end] for r in left_ranges]
        assert any("100" in t for t in modify_texts), (
            f"'100' should be detected, got: {left_ranges}"
        )

    def test_d017_cross_line_no_fragmentation(self) -> None:
        """D017-style OCR fragments should not cross-match with address parts."""
        left = "甲方:公司A\n地址:南京市江宁经济技术开发区水阁路"
        right = "甲方:公司B\n地址:北京市海淀区"
        _, _, left_ranges, right_ranges = changed_snippets(left, right)
        left_all = "".join(left[r.start:r.end] for r in left_ranges)
        # "江宁经济技术开发区水阁路" is the real change, not fragmented cross-line matches
        assert "江宁" in left_all or "水阁" in left_all or "南京" in left_all, (
            f"Expected address change, got left ranges: {left_ranges}"
        )
        # No cross-line contamination from address into company name
        right_all = "".join(right[r.start:r.end] for r in right_ranges)
        assert "北京" in right_all or "海淀" in right_all, (
            f"Expected address change on right, got: {right_ranges}"
        )
