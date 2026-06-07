from __future__ import annotations

import difflib
import logging
import os
import re

from app.models import TextRange

from app.services.diff.text_utils import (
    build_compacted_text,
    build_diff_text,
    compacted_range_to_original,
    is_whitespace_only,
    merge_ranges,
    shorten,
    trim_range_whitespace,
)

try:
    from diff_match_patch import diff_match_patch as _DiffMatchPatch
except Exception:  # pragma: no cover - dependency may be absent in fallback deployments
    _DiffMatchPatch = None

DIFF_MATCH_PATCH_TIMEOUT_SECONDS = 1.0
logger = logging.getLogger(__name__)


def changed_snippets(left: str, right: str) -> tuple[str, str, list[TextRange], list[TextRange]]:
    if configured_diff_engine() == "diff_match_patch":
        try:
            return changed_snippets_diff_match_patch(left, right)
        except Exception:
            logger.warning("diff-match-patch failed; falling back to difflib", exc_info=True)
            return changed_snippets_difflib(left, right)
    return changed_snippets_difflib(left, right)


def configured_diff_engine() -> str:
    env_value = os.getenv("DIFF_ENGINE")
    if env_value:
        return normalize_diff_engine(env_value)
    try:
        from app.config import settings

        return normalize_diff_engine(settings.diff_engine)
    except Exception:
        return "diff_match_patch"


def normalize_diff_engine(value: str) -> str:
    engine = str(value or "diff_match_patch").strip().lower().replace("-", "_")
    if engine in {"diff_match_patch", "difflib"}:
        return engine
    logger.warning("Invalid DIFF_ENGINE=%r; falling back to diff_match_patch", value)
    return "diff_match_patch"


def changed_snippets_diff_match_patch(left: str, right: str) -> tuple[str, str, list[TextRange], list[TextRange]]:
    if _DiffMatchPatch is None:
        raise RuntimeError("diff-match-patch is not available")
    left_compacted, left_segments = build_compacted_text(left)
    right_compacted, right_segments = build_compacted_text(right)
    opcodes = diff_match_patch_opcodes(left_compacted, right_compacted)
    return changed_snippets_from_opcodes(left, right, left_segments, right_segments, opcodes)


def changed_snippets_difflib(left: str, right: str) -> tuple[str, str, list[TextRange], list[TextRange]]:
    left_compacted, left_segments = build_compacted_text(left)
    right_compacted, right_segments = build_compacted_text(right)

    matcher = difflib.SequenceMatcher(None, left_compacted, right_compacted, autojunk=False)
    return changed_snippets_from_opcodes(left, right, left_segments, right_segments, matcher.get_opcodes())


def changed_snippets_from_opcodes(
    left: str,
    right: str,
    left_segments: list[tuple[int, int]],
    right_segments: list[tuple[int, int]],
    opcodes: list[tuple[str, int, int, int, int]],
) -> tuple[str, str, list[TextRange], list[TextRange]]:
    left_ranges: list[TextRange] = []
    right_ranges: list[TextRange] = []
    for hunk in change_hunks(opcodes):
        c_left_start, c_left_end = hunk[0][1], hunk[-1][2]
        c_right_start, c_right_end = hunk[0][3], hunk[-1][4]

        has_left_change = any(i1 < i2 for tag, i1, i2, _, _ in hunk if tag != "insert")
        has_right_change = any(j1 < j2 for tag, _, _, j1, j2 in hunk if tag != "delete")

        left_start, left_end = compacted_range_to_original(
            c_left_start, c_left_end, left_segments
        )
        right_start, right_end = compacted_range_to_original(
            c_right_start, c_right_end, right_segments
        )

        left_start, left_end = trim_range_whitespace(left, left_start, left_end)
        right_start, right_end = trim_range_whitespace(right, right_start, right_end)

        if has_left_change and is_whitespace_only(left, left_start, left_end):
            has_left_change = False
        if has_right_change and is_whitespace_only(right, right_start, right_end):
            has_right_change = False

        if not has_left_change and not has_right_change:
            continue

        if has_left_change and has_right_change:
            if (
                should_force_inline_for_composite_hunk(hunk)
                and "\n" not in left[left_start:left_end]
                and "\n" not in right[right_start:right_end]
            ):
                refined_left_ranges, refined_right_ranges = refine_inline_changed_ranges(
                    left, left_start, left_end, right, right_start, right_end,
                )
            else:
                refined_left_ranges, refined_right_ranges = refine_changed_ranges(
                    left, left_start, left_end, right, right_start, right_end,
                )
            left_ranges.extend(refined_left_ranges)
            right_ranges.extend(refined_right_ranges)
        elif has_left_change:
            left_ranges.append(expand_token_range(left, left_start, left_end, "DELETE"))
        elif has_right_change:
            right_ranges.append(expand_token_range(right, right_start, right_end, "ADD"))

    left_ranges = merge_ranges(left_ranges)
    right_ranges = merge_ranges(right_ranges)
    return (
        shorten("".join(left[item.start : item.end] for item in left_ranges)),
        shorten("".join(right[item.start : item.end] for item in right_ranges)),
        left_ranges,
        right_ranges,
    )


def diff_match_patch_opcodes(left: str, right: str) -> list[tuple[str, int, int, int, int]]:
    if _DiffMatchPatch is None:
        raise RuntimeError("diff-match-patch is not available")
    dmp = _DiffMatchPatch()
    dmp.Diff_Timeout = DIFF_MATCH_PATCH_TIMEOUT_SECONDS
    diffs = dmp.diff_main(left, right)
    dmp.diff_cleanupSemantic(diffs)
    opcodes: list[tuple[str, int, int, int, int]] = []
    left_pos = 0
    right_pos = 0
    for operation, text in diffs:
        length = len(text)
        if operation == 0:
            opcodes.append(("equal", left_pos, left_pos + length, right_pos, right_pos + length))
            left_pos += length
            right_pos += length
        elif operation == -1:
            opcodes.append(("delete", left_pos, left_pos + length, right_pos, right_pos))
            left_pos += length
        elif operation == 1:
            opcodes.append(("insert", left_pos, left_pos, right_pos, right_pos + length))
            right_pos += length
    if left_pos != len(left) or right_pos != len(right):
        raise RuntimeError("diff-match-patch returned inconsistent cursor positions")
    return opcodes


def change_hunks(
    opcodes: list[tuple[str, int, int, int, int]],
) -> list[list[tuple[str, int, int, int, int]]]:
    hunks: list[list[tuple[str, int, int, int, int]]] = []
    current: list[tuple[str, int, int, int, int]] = []
    pending_equal: tuple[str, int, int, int, int] | None = None

    for opcode in opcodes:
        tag, i1, i2, j1, j2 = opcode
        if tag == "equal":
            if current and is_short_bridge(i2 - i1, j2 - j1):
                pending_equal = opcode
            else:
                if current:
                    hunks.append(current)
                    current = []
                pending_equal = None
            continue
        if pending_equal is not None:
            current.append(pending_equal)
            pending_equal = None
        current.append(opcode)

    if current:
        hunks.append(current)
    return hunks


def is_short_bridge(left_len: int, right_len: int) -> bool:
    return max(left_len, right_len) <= 5


def should_force_inline_for_composite_hunk(hunk: list[tuple[str, int, int, int, int]]) -> bool:
    changed_parts = [opcode for opcode in hunk if opcode[0] != "equal"]
    return len(changed_parts) > 1 and any(opcode[0] == "equal" for opcode in hunk)


def refine_changed_ranges(
    left: str, left_start: int, left_end: int,
    right: str, right_start: int, right_end: int,
) -> tuple[list[TextRange], list[TextRange]]:
    multiline_ranges = refine_multiline_changed_ranges(
        left, left_start, left_end, right, right_start, right_end,
    )
    if multiline_ranges is not None:
        return multiline_ranges

    if should_refine_inline(left[left_start:left_end], right[right_start:right_end]):
        return refine_inline_changed_ranges(
            left, left_start, left_end, right, right_start, right_end,
        )

    return coarse_modify_ranges(left, left_start, left_end, right, right_start, right_end)


def coarse_modify_ranges(
    left: str, left_start: int, left_end: int,
    right: str, right_start: int, right_end: int,
) -> tuple[list[TextRange], list[TextRange]]:
    if is_numeric_value_change(left, left_start, left_end, right, right_start, right_end):
        return (
            [expand_numeric_value_range(left, left_start, left_end, "MODIFY")],
            [expand_numeric_value_range(right, right_start, right_end, "MODIFY")],
        )
    if is_percent_unit_change(left, left_start, left_end, right, right_start, right_end):
        return (
            [expand_numeric_unit_range(left, left_start, left_end, "MODIFY")],
            [expand_numeric_unit_range(right, right_start, right_end, "MODIFY")],
        )
    return (
        [expand_token_range(left, left_start, left_end, "MODIFY", right, right_start)],
        [expand_token_range(right, right_start, right_end, "MODIFY", left, left_start)],
    )


def should_refine_inline(left: str, right: str) -> bool:
    left_compact = re.sub(r"\s+", "", left or "")
    right_compact = re.sub(r"\s+", "", right or "")
    if not left_compact or not right_compact:
        return False
    return (
        left_compact in right_compact
        or right_compact in left_compact
        or has_shared_cjk_sequence(left_compact, right_compact)
    )


def has_shared_cjk_sequence(left: str, right: str) -> bool:
    match = difflib.SequenceMatcher(None, left, right).find_longest_match(0, len(left), 0, len(right))
    if match.size < 2:
        return False
    shared = left[match.a : match.a + match.size]
    return sum(1 for char in shared if "一" <= char <= "鿿") >= 2


def refine_inline_changed_ranges(
    left: str, left_start: int, left_end: int,
    right: str, right_start: int, right_end: int,
) -> tuple[list[TextRange], list[TextRange]]:
    local_left = left[left_start:left_end]
    local_right = right[right_start:right_end]
    local_left_compacted, local_left_segments = build_diff_text(local_left)
    local_right_compacted, local_right_segments = build_diff_text(local_right)
    if not local_left_compacted or not local_right_compacted:
        return (
            [expand_token_range(left, left_start, left_end, "DELETE")] if local_left_compacted else [],
            [expand_token_range(right, right_start, right_end, "ADD")] if local_right_compacted else [],
        )

    matcher = difflib.SequenceMatcher(None, local_left_compacted, local_right_compacted)
    left_ranges: list[TextRange] = []
    right_ranges: list[TextRange] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        current_left_start, current_left_end = local_range_to_original(
            i1, i2, local_left_segments, left_start,
        )
        current_right_start, current_right_end = local_range_to_original(
            j1, j2, local_right_segments, right_start,
        )
        current_left_start, current_left_end = trim_range_whitespace(left, current_left_start, current_left_end)
        current_right_start, current_right_end = trim_range_whitespace(right, current_right_start, current_right_end)

        has_left = tag != "insert" and current_left_start < current_left_end and not is_whitespace_only(left, current_left_start, current_left_end)
        has_right = tag != "delete" and current_right_start < current_right_end and not is_whitespace_only(right, current_right_start, current_right_end)
        if has_left and has_right:
            if is_numeric_value_change(
                left, current_left_start, current_left_end,
                right, current_right_start, current_right_end,
            ):
                left_ranges.append(expand_numeric_value_range(left, current_left_start, current_left_end, "MODIFY"))
                right_ranges.append(expand_numeric_value_range(right, current_right_start, current_right_end, "MODIFY"))
            elif is_percent_unit_change(
                left, current_left_start, current_left_end,
                right, current_right_start, current_right_end,
            ):
                left_ranges.append(expand_numeric_unit_range(left, current_left_start, current_left_end, "MODIFY"))
                right_ranges.append(expand_numeric_unit_range(right, current_right_start, current_right_end, "MODIFY"))
            else:
                left_ranges.append(
                    expand_token_range(left, current_left_start, current_left_end, "MODIFY", right, current_right_start)
                )
                right_ranges.append(
                    expand_token_range(right, current_right_start, current_right_end, "MODIFY", left, current_left_start)
                )
        elif has_left:
            left_ranges.append(expand_token_range(left, current_left_start, current_left_end, "DELETE"))
        elif has_right:
            right_ranges.append(expand_token_range(right, current_right_start, current_right_end, "ADD"))
    return left_ranges, right_ranges


def refine_multiline_changed_ranges(
    left: str, left_start: int, left_end: int,
    right: str, right_start: int, right_end: int,
) -> tuple[list[TextRange], list[TextRange]] | None:
    left_lines = line_ranges(left, left_start, left_end)
    right_lines = line_ranges(right, right_start, right_end)
    if len(left_lines) <= 1 or len(right_lines) <= 1:
        return None
    if any(len(line) > 40 for line, _, _ in [*left_lines, *right_lines]):
        return None

    pairs = match_similar_lines(left_lines, right_lines)
    if not pairs:
        return None

    left_ranges: list[TextRange] = []
    right_ranges: list[TextRange] = []
    previous_left = 0
    previous_right = 0
    for left_index, right_index in pairs:
        left_ranges.extend(ranges_for_unmatched_lines(left, left_lines, previous_left, left_index, "DELETE"))
        right_ranges.extend(ranges_for_unmatched_lines(right, right_lines, previous_right, right_index, "ADD"))

        line_left_ranges, line_right_ranges = refine_inline_changed_ranges(
            left, left_lines[left_index][1], left_lines[left_index][2],
            right, right_lines[right_index][1], right_lines[right_index][2],
        )
        left_ranges.extend(line_left_ranges)
        right_ranges.extend(line_right_ranges)
        previous_left = left_index + 1
        previous_right = right_index + 1

    left_ranges.extend(ranges_for_unmatched_lines(left, left_lines, previous_left, len(left_lines), "DELETE"))
    right_ranges.extend(ranges_for_unmatched_lines(right, right_lines, previous_right, len(right_lines), "ADD"))
    return left_ranges, right_ranges


def line_ranges(text: str, start: int, end: int) -> list[tuple[str, int, int]]:
    ranges: list[tuple[str, int, int]] = []
    cursor = start
    for raw_line in text[start:end].splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if stripped:
            line_start = cursor + line.find(stripped)
            line_end = line_start + len(stripped)
            ranges.append((stripped, line_start, line_end))
        cursor += len(raw_line)
    return ranges


def match_similar_lines(
    left_lines: list[tuple[str, int, int]],
    right_lines: list[tuple[str, int, int]],
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    next_right = 0
    for left_index, (left_line, _, _) in enumerate(left_lines):
        best_index: int | None = None
        best_score = 0.0
        for right_index in range(next_right, len(right_lines)):
            right_line = right_lines[right_index][0]
            score = line_similarity(left_line, right_line)
            if score > best_score:
                best_score = score
                best_index = right_index
        if best_index is None or best_score < 0.5:
            continue
        pairs.append((left_index, best_index))
        next_right = best_index + 1
    return pairs


def line_similarity(left: str, right: str) -> float:
    left_compact = re.sub(r"\s+", "", left or "")
    right_compact = re.sub(r"\s+", "", right or "")
    if not left_compact or not right_compact:
        return 0.0
    if len(shared_meaningful_chars(left_compact, right_compact)) < 2:
        return 0.0
    return difflib.SequenceMatcher(None, left_compact, right_compact).ratio()


def shared_meaningful_chars(left: str, right: str) -> set[str]:
    return {
        char
        for char in set(left) & set(right)
        if char.isalnum() or "一" <= char <= "鿿"
    }


def ranges_for_unmatched_lines(
    text: str,
    lines: list[tuple[str, int, int]],
    start_index: int,
    end_index: int,
    highlight_type: str,
) -> list[TextRange]:
    ranges: list[TextRange] = []
    for _, start, end in lines[start_index:end_index]:
        ranges.append(expand_token_range(text, start, end, highlight_type))
    return ranges


def local_range_to_original(
    start: int, end: int,
    segments: list[tuple[int, int]],
    offset: int,
) -> tuple[int, int]:
    if start >= end or not segments:
        return offset, offset
    local_start, local_end = compacted_range_to_original(start, end, segments)
    return offset + local_start, offset + local_end


def expand_token_range(
    text: str, start: int, end: int,
    highlight_type: str,
    other_text: str | None = None,
    other_pos: int | None = None,
) -> TextRange:
    original_start, original_end = start, end
    while start > 0 and is_ascii_token_char(text[start - 1]):
        start -= 1
    while end < len(text) and is_ascii_token_char(text[end]):
        end += 1

    if other_text is not None and other_pos is not None:
        expanded_left = text[start:original_start]
        if expanded_left:
            search_start = max(0, other_pos - len(expanded_left) - 2)
            search_end = other_pos + len(expanded_left) + 2
            if other_text.find(expanded_left, search_start, search_end) >= 0:
                start = original_start
        expanded_right = text[original_end:end]
        if expanded_right:
            search_start = max(0, other_pos - 2)
            search_end = other_pos + len(expanded_right) + 2
            if other_text.find(expanded_right, search_start, search_end) >= 0:
                end = original_end

    return TextRange(start=start, end=end, highlight_type=highlight_type)


def is_ascii_token_char(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in "._-/%")


def is_percent_unit_change(
    left: str, left_start: int, left_end: int,
    right: str, right_start: int, right_end: int,
) -> bool:
    left_unit = left[left_start:left_end]
    right_unit = right[right_start:right_end]
    if {left_unit, right_unit} != {"%", "‰"}:
        return False
    return has_numeric_prefix(left, left_start) and has_numeric_prefix(right, right_start)


def has_numeric_prefix(text: str, index: int) -> bool:
    return index > 0 and text[index - 1].isdigit()


def is_numeric_value_change(
    left: str,
    left_start: int,
    left_end: int,
    right: str,
    right_start: int,
    right_end: int,
) -> bool:
    return has_number_context(left, left_start, left_end) and has_number_context(right, right_start, right_end)


def has_number_context(text: str, start: int, end: int) -> bool:
    if any(char.isdigit() for char in text[start:end]):
        return True
    return (start > 0 and text[start - 1].isdigit()) or (end < len(text) and text[end].isdigit())


def expand_numeric_value_range(text: str, start: int, end: int, highlight_type: str) -> TextRange:
    while start > 0 and is_number_context_char(text[start - 1]):
        start -= 1
    while end < len(text) and is_number_context_char(text[end]):
        end += 1
    return TextRange(start=start, end=end, highlight_type=highlight_type)


def expand_numeric_unit_range(text: str, start: int, end: int, highlight_type: str) -> TextRange:
    while start > 0 and is_number_context_char(text[start - 1]):
        start -= 1
    return TextRange(start=start, end=end, highlight_type=highlight_type)


def is_number_context_char(char: str) -> bool:
    return char.isdigit() or char in ".,"
