from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from app.services.clause_alignment import ClauseAlignmentFingerprint
from app.services.clause_numbering import ClauseNumberParser
from app.services.normalizer import TextNormalizer


FORMAL_DECIMAL_MARKER_PATTERN = re.compile(r"^\s*(?P<marker>第\d+(?:\.\d+)+[章节条])\s*(?P<title>.*)$")


def canonical_path_label(label: str, number_parser: ClauseNumberParser) -> str:
    parsed = number_parser.parse_line(label)
    if parsed is None:
        formal_decimal = FORMAL_DECIMAL_MARKER_PATTERN.match(unicodedata.normalize("NFKC", label or ""))
        if formal_decimal is not None:
            canonical = number_parser.normalize_number(formal_decimal.group("marker")).replace(".", "_")
            title = formal_decimal.group("title").strip()
            if title:
                return f"n{canonical} {title}"
            return f"n{canonical}"
        return label
    canonical = f"n{parsed.canonical_number.replace('.', '_')}"
    if parsed.title:
        return f"{canonical} {parsed.title}"
    return canonical


class ClauseSectionPathBuilder:
    def __init__(
        self,
        *,
        number_parser: ClauseNumberParser,
        is_formal_clause_marker: Callable[[str], bool],
    ) -> None:
        self.number_parser = number_parser
        self.is_formal_clause_marker = is_formal_clause_marker
        self.section_paths: dict[str, list[str]] = {}
        self.section_levels: dict[str, list[tuple[int, str]]] = {}

    def path(
        self,
        *,
        section_type: str,
        clause_no: str,
        title: str,
        level: int | None = None,
    ) -> list[str]:
        label = self._path_label(clause_no, title)
        if not label:
            return list(self.section_paths.get(section_type, []))
        stack = list(self.section_levels.get(section_type, []))
        marker_level = self._section_marker_level(clause_no, level, stack)
        while stack and stack[-1][0] >= marker_level:
            stack.pop()
        stack.append((marker_level, label))
        self.section_levels[section_type] = stack
        current = [item[1] for item in stack]
        self.section_paths[section_type] = current
        return current

    def _section_marker_level(
        self,
        clause_no: str,
        level: int | None,
        stack: list[tuple[int, str]],
    ) -> int:
        marker_level = level if level is not None else self.number_parser.level_of(clause_no)
        normalized = self.number_parser.normalize_number(clause_no)
        if re.fullmatch(r"\d+(?:\.\d+)+", normalized):
            formal_parent_level = self._nearest_formal_parent_level(stack)
            if formal_parent_level:
                marker_level = max(marker_level, formal_parent_level + normalized.count("."))
        return marker_level

    def _nearest_formal_parent_level(self, stack: list[tuple[int, str]]) -> int:
        for level, label in reversed(stack):
            marker = label.split(" ", 1)[0]
            if self.is_formal_clause_marker(marker):
                return level
        return 0

    @staticmethod
    def _path_label(clause_no: str, title: str) -> str:
        compact_title = re.sub(r"\s+", "", title or "")[:24]
        if clause_no and compact_title:
            return f"{clause_no} {compact_title}"
        return clause_no or compact_title


class ClauseKeyBuilder:
    def __init__(
        self,
        *,
        normalizer: TextNormalizer,
        number_parser: ClauseNumberParser,
        title_from_text: Callable[[str], str],
    ) -> None:
        self.normalizer = normalizer
        self.number_parser = number_parser
        self.title_from_text = title_from_text

    def clause_key(self, section_type: str, section_path: list[str], clause_no: str, text: str) -> str:
        parts = [section_type or "main_contract"]
        path = [self.normalizer.normalize_for_match(self.canonical_path_label(item)) for item in section_path if item]
        if path:
            parts.extend(path)
        elif clause_no:
            canonical = self.number_parser.normalize_number(clause_no).replace(".", "_")
            parts.append(self.normalizer.normalize_for_match(f"n{canonical}"))
        else:
            parts.append(self.normalizer.normalize_for_match(self.title_from_text(text))[:32])
        return "/".join(part for part in parts if part)

    def alignment_clause_key(self, base_key: str, fingerprint: ClauseAlignmentFingerprint) -> str:
        parts = [base_key]
        clause_no_key = self.alignment_clause_no_key(fingerprint.clause_no_key)
        if clause_no_key and clause_no_key not in base_key:
            parts.append(clause_no_key)
        parts.extend(self.bounded_alignment_tokens(fingerprint.critical_tokens))
        return "|".join(parts)

    def alignment_clause_no_key(self, clause_no_key: str) -> str:
        if not clause_no_key:
            return ""
        return self.normalizer.normalize_for_match(f"n{clause_no_key.replace('.', '_')}")

    @staticmethod
    def bounded_alignment_tokens(tokens: tuple[str, ...]) -> list[str]:
        bounded: list[str] = []
        total_length = 0
        max_total_length = 180
        max_token_length = 72
        for token in tokens:
            clean_token = token.strip()[:max_token_length]
            if not clean_token:
                continue
            next_length = total_length + len(clean_token)
            if next_length > max_total_length:
                break
            bounded.append(clean_token)
            total_length = next_length
        return bounded

    def canonical_path_label(self, label: str) -> str:
        return canonical_path_label(label, self.number_parser)
