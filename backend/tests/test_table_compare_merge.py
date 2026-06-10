"""Tests for table row merging detection and reconciliation."""

from __future__ import annotations

import pytest

from app.services.table_compare.matcher import TableMatcher
from app.services.table_compare import utils


class TestSplitMergedCellText:
    """_split_merged_cell_text correctly splits multi-value cells."""

    def test_single_line_unchanged(self) -> None:
        assert TableMatcher._split_merged_cell_text("中期模型") == ["中期模型"]

    def test_newline_splits_when_lengths_close(self) -> None:
        # "中期模型" (4 chars) and "短期模型" (4 chars) — similar lengths
        result = TableMatcher._split_merged_cell_text("中期模型\n短期模型")
        assert result == ["中期模型", "短期模型"]

    def test_newline_not_split_when_lengths_too_different(self) -> None:
        # Short value vs long value — should NOT split
        result = TableMatcher._split_merged_cell_text("短\n这是一个非常长的段落描述文本内容")
        assert result == ["短\n这是一个非常长的段落描述文本内容"]

    def test_newline_splits_three_values(self) -> None:
        result = TableMatcher._split_merged_cell_text("A\nB\nC")
        assert result == ["A", "B", "C"]

    def test_empty_sub_texts_filtered(self) -> None:
        result = TableMatcher._split_merged_cell_text("中期模型\n\n短期模型\n")
        assert result == ["中期模型", "短期模型"]


class TestCellMergeSimilarity:
    """_cell_merge_similarity detects subset relationships."""

    def test_exact_subset(self) -> None:
        # "中期模型" is a subset of "中期模型|短期模型"
        score = TableMatcher._cell_merge_similarity("中期模型", "中期模型|短期模型")
        assert score >= 0.90

    def test_reverse_subset(self) -> None:
        score = TableMatcher._cell_merge_similarity("中期模型|短期模型", "短期模型")
        assert score >= 0.90

    def test_identical(self) -> None:
        score = TableMatcher._cell_merge_similarity("中期模型|短期模型", "中期模型|短期模型")
        assert score == 1.0

    def test_unrelated(self) -> None:
        score = TableMatcher._cell_merge_similarity("中期模型", "超短期模型")
        assert score == 0.0

    def test_partial_overlap_not_subset(self) -> None:
        # "A|B" vs "A|C" — B not in second, C not in first, no subset relationship
        score = TableMatcher._cell_merge_similarity("A|B", "A|C")
        assert score == 0.0


class TestIsSubsetMatch:
    """is_subset_match helper in utils."""

    def test_subset_detected(self) -> None:
        assert utils.is_subset_match("中期模型", "中期模型|短期模型") is True

    def test_reverse_subset_detected(self) -> None:
        assert utils.is_subset_match("中期模型|短期模型", "短期模型") is True

    def test_unrelated(self) -> None:
        assert utils.is_subset_match("中期模型", "超短期模型") is False

    def test_empty(self) -> None:
        assert utils.is_subset_match("", "中期模型") is False
        assert utils.is_subset_match("中期模型", "") is False

class TestSplitMergedCellTextSpace:
    """Space-separated merged cell text handling."""

    def test_space_separated_short_tokens(self) -> None:
        result = TableMatcher._split_merged_cell_text("中期模型 短期模型")
        assert result == ["中期模型", "短期模型"]

    def test_space_in_long_text_not_split(self) -> None:
        # Long parts should not be split to avoid breaking descriptions
        result = TableMatcher._split_merged_cell_text("风电场中期功率预报 模型开发")
        assert result == ["风电场中期功率预报 模型开发"]

    def test_newline_takes_precedence(self) -> None:
        result = TableMatcher._split_merged_cell_text("中期模型\n短期模型 超短期模型")
        assert result == ["中期模型", "短期模型 超短期模型"]


class TestLenientCellMergeSimilarity:
    """Lenient _cell_merge_similarity with content token matching."""

    def test_content_token_match(self) -> None:
        # orig row 3: token is "短期模型" → matched in compare "中期模型|短期模型"
        score = TableMatcher._cell_merge_similarity(
            "3|短期模型|风电场短期功率预报模型开发|国能日新|套|1",
            "2|中期模型|短期模型|风电场中期功率预报模型开发|国能日新|套|1"
        )
        assert score >= 0.88, f"Expected >= 0.88, got {score}"

    def test_no_content_token_match(self) -> None:
        # Compare side is a strict subset of original side (missing remark)
        # _cell_merge_similarity correctly returns a high score for subset
        # But the reconciliation checks direction: unmatched side must be subset
        score = TableMatcher._cell_merge_similarity(
            "1|工作站|cpu:hg3350|中国|中科可控|20000|20000|国产芯片",
            "1|工作站|cpu:hg3350|中国|中科可控|20000|20000"
        )
        assert score >= 0.90, f"Subset should get high score, got {score}"

    def test_content_token_match_related_rows(self) -> None:
        # Theory: "理论可用功率计算" token in both sides, descriptions differ
        score = TableMatcher._cell_merge_similarity(
            "6|理论可用功率计算|理论可用功率计算|国能日新|套|1",
            "6|理论可用功率计算|接口开放及系统开发|国能日新|套|1"
        )
        assert score >= 0.88, f"Expected >= 0.88, got {score}"
