from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from app.models import Clause
from app.services.normalizer import TextNormalizer


@dataclass(frozen=True)
class ClauseAlignmentFingerprint:
    clause_no_key: str
    title_key: str
    normalized_body_key: str
    body_fingerprint: str
    critical_tokens: tuple[str, ...]
    critical_token_fingerprint: str
    structure_key: str
    page_span: tuple[int, int] | None


class ClauseAlignmentAnalyzer:
    _THOUSANDS_SEPARATOR_RE = re.compile(r"(?<=\d),(?=\d{3}(?:\D|$))")
    _WHITESPACE_RE = re.compile(r"\s+")
    _STABLE_KEY_NOISE_RE = re.compile(
        r"[\s_\-:/\\|,.;!?()\[\]{}<>，。；：、？！“”‘’（）【】《》]+"
    )
    _CHINESE_DATE_RE = re.compile(
        r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日?"
    )
    _NUMERIC_DATE_RE = re.compile(
        r"(?<!\d)(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})(?!\d)"
    )
    _AMOUNT_RE = re.compile(
        r"(?:人民币|金额|价款|费用|总价)?\s*(?:为|是|:|：)?\s*"
        r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>万元|元|人民币)"
    )
    _PERCENT_RE = re.compile(r"(?<![\d.])(?P<percent>\d+(?:\.\d+)?)\s*%")
    _CONTRACT_NO_RE = re.compile(
        r"(?:合同编号|编号)\s*(?:[:：]|为|是)?\s*"
        r"(?P<contract_no>[A-Za-z0-9][A-Za-z0-9_\-./]*)"
    )
    _TERM_RE = re.compile(
        r"(?:期限|有效期|服务期)\s*(?:为|是|:|：)?\s*"
        r"(?P<term>[零〇一二三四五六七八九十百千万两\d]+)\s*"
        r"(?P<unit>个工作日|个月|年|月|日|天)"
    )
    _PARTY_RE = re.compile(
        r"(?P<role>甲方|乙方)\s*(?:[:：]|为)\s*"
        r"(?P<party>[\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,60}?"
        r"(?:有限责任公司|股份有限公司|有限公司|公司|集团))"
    )
    _QUANTITY_RE = re.compile(
        r"(?P<quantity>\d+(?:\.\d+)?)\s*(?P<unit>台|套|个(?!工作日)|件|项|批|份)"
    )

    def __init__(self, normalizer: TextNormalizer | None = None) -> None:
        self.normalizer = normalizer or TextNormalizer()

    def fingerprint(self, clause: Clause) -> ClauseAlignmentFingerprint:
        clause_no_key = self._clause_no_key(clause.clause_no)
        title_key = self.normalizer.normalize_for_match(clause.title)
        normalized_body_key = self._normalized_body(clause.normalized_text or clause.text)
        critical_tokens = self._critical_tokens(clause.text or clause.normalized_text)
        critical_token_fingerprint = "|".join(critical_tokens)

        return ClauseAlignmentFingerprint(
            clause_no_key=clause_no_key,
            title_key=title_key,
            normalized_body_key=normalized_body_key,
            body_fingerprint=normalized_body_key[:160],
            critical_tokens=critical_tokens,
            critical_token_fingerprint=critical_token_fingerprint,
            structure_key=self._stable_key(clause.section_type),
            page_span=self._page_span(clause.page_numbers),
        )

    def text_similarity(self, left: str | Clause, right: str | Clause) -> float:
        left_text = self._text_value(left)
        right_text = self._text_value(right)
        left_key = self._normalized_body(left_text)
        right_key = self._normalized_body(right_text)
        if not left_key and not right_key:
            return 1.0
        return round(SequenceMatcher(None, left_key, right_key).ratio(), 4)

    def token_overlap(self, left_tokens: Iterable[str], right_tokens: Iterable[str]) -> float:
        left = set(left_tokens)
        right = set(right_tokens)
        if not left and not right:
            return 1.0
        if not left or not right:
            return 0.0
        return round(len(left & right) / len(left | right), 4)

    def diagnostics(self, original: Clause, compare: Clause) -> dict[str, Any]:
        original_fingerprint = self.fingerprint(original)
        compare_fingerprint = self.fingerprint(compare)
        body_similarity = self.text_similarity(original, compare)
        critical_token_overlap = self.token_overlap(
            original_fingerprint.critical_tokens,
            compare_fingerprint.critical_tokens,
        )

        return {
            "number_match": original_fingerprint.clause_no_key == compare_fingerprint.clause_no_key,
            "title_match": original_fingerprint.title_key == compare_fingerprint.title_key,
            "body_similarity": body_similarity,
            "critical_token_overlap": critical_token_overlap,
            "section_type_match": original_fingerprint.structure_key == compare_fingerprint.structure_key,
            "page_distance": self._page_distance(original_fingerprint.page_span, compare_fingerprint.page_span),
            "risk_flags": self.risk_flags(
                original,
                compare,
                body_similarity=body_similarity,
                critical_token_overlap=critical_token_overlap,
                original_fingerprint=original_fingerprint,
                compare_fingerprint=compare_fingerprint,
            ),
        }

    def risk_flags(
        self,
        original: Clause,
        compare: Clause,
        *,
        body_similarity: float | None = None,
        critical_token_overlap: float | None = None,
        original_fingerprint: ClauseAlignmentFingerprint | None = None,
        compare_fingerprint: ClauseAlignmentFingerprint | None = None,
    ) -> list[str]:
        original_fingerprint = original_fingerprint or self.fingerprint(original)
        compare_fingerprint = compare_fingerprint or self.fingerprint(compare)
        body_similarity = self.text_similarity(original, compare) if body_similarity is None else body_similarity
        critical_token_overlap = (
            self.token_overlap(original_fingerprint.critical_tokens, compare_fingerprint.critical_tokens)
            if critical_token_overlap is None
            else critical_token_overlap
        )

        flags: list[str] = []
        original_clause_no = original_fingerprint.clause_no_key
        compare_clause_no = compare_fingerprint.clause_no_key
        if original_clause_no and compare_clause_no and original_clause_no != compare_clause_no and body_similarity >= 0.78:
            flags.append("TEXT_MATCH_NUMBER_MISMATCH")
        if original_clause_no and compare_clause_no and original_clause_no == compare_clause_no and body_similarity < 0.45:
            flags.append("TITLE_MATCH_TEXT_MISMATCH")
        if original_fingerprint.critical_tokens and compare_fingerprint.critical_tokens and critical_token_overlap < 0.5:
            flags.append("CRITICAL_TOKEN_MISMATCH")
        if flags:
            flags.append("POSSIBLE_CLAUSE_MISALIGNMENT")
        return flags

    def _normalized_body(self, text: str) -> str:
        normalized = self.normalizer.normalize_for_diff(text)
        normalized = unicodedata.normalize("NFKC", normalized)
        normalized = self._THOUSANDS_SEPARATOR_RE.sub("", normalized)
        return self._WHITESPACE_RE.sub("", normalized).lower()

    def _critical_tokens(self, text: str) -> tuple[str, ...]:
        normalized = unicodedata.normalize("NFKC", text or "")
        tokens: set[str] = set()

        for match in self._CHINESE_DATE_RE.finditer(normalized):
            tokens.add(self._date_token(match))
        for match in self._NUMERIC_DATE_RE.finditer(normalized):
            tokens.add(self._date_token(match))
        for match in self._AMOUNT_RE.finditer(normalized):
            amount = match.group("amount").replace(",", "")
            tokens.add(f"amount:{amount}")
            tokens.add(f"amount_unit:{amount}{match.group('unit')}")
        for match in self._PERCENT_RE.finditer(normalized):
            percent = self._normalize_number(match.group("percent"))
            tokens.add(f"percent:{percent}%")
        for match in self._CONTRACT_NO_RE.finditer(normalized):
            tokens.add(f"contract_no:{match.group('contract_no')}")
        for match in self._TERM_RE.finditer(normalized):
            tokens.add(f"term:{match.group('term')}{match.group('unit')}")
        for match in self._PARTY_RE.finditer(normalized):
            role = match.group("role")
            party = match.group("party")
            tokens.add(f"party:{role}:{party}")
        for match in self._QUANTITY_RE.finditer(normalized):
            quantity = self._normalize_number(match.group("quantity"))
            tokens.add(f"quantity:{quantity}{match.group('unit')}")

        return tuple(sorted(tokens))

    def _date_token(self, match: re.Match[str]) -> str:
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        return f"date:{year:04d}-{month:02d}-{day:02d}"

    def _normalize_number(self, value: str) -> str:
        if "." not in value:
            return value
        return value.rstrip("0").rstrip(".")

    def _compact(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "")
        return self._WHITESPACE_RE.sub("", normalized).lower()

    def _clause_no_key(self, text: str) -> str:
        compact = self._compact(text)
        match = re.fullmatch(r"第(?P<clause_no>.+)条", compact)
        if match:
            return match.group("clause_no")
        return compact

    def _stable_key(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "").lower()
        return self._STABLE_KEY_NOISE_RE.sub("", normalized)

    def _page_span(self, page_numbers: list[int]) -> tuple[int, int] | None:
        if not page_numbers:
            return None
        return (min(page_numbers), max(page_numbers))

    def _page_distance(
        self,
        left: tuple[int, int] | None,
        right: tuple[int, int] | None,
    ) -> int | None:
        if left is None or right is None:
            return None
        left_start, left_end = left
        right_start, right_end = right
        if left_start <= right_end and right_start <= left_end:
            return 0
        if left_end < right_start:
            return right_start - left_end
        return left_start - right_end

    def _text_value(self, value: str | Clause) -> str:
        if isinstance(value, Clause):
            return value.normalized_text or value.text
        return value
