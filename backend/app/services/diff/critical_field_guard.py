from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

FIELD_AMOUNT = "AMOUNT"
FIELD_DATE = "DATE"
FIELD_PERCENT_RATE = "PERCENT_RATE"
FIELD_DURATION = "DURATION"
FIELD_QUANTITY = "QUANTITY"
FIELD_PARTY_ROLE = "PARTY_ROLE"

CRITICAL_FIELD_CHANGE = "CRITICAL_FIELD_CHANGE"

FIELD_ORDER = [
    FIELD_AMOUNT,
    FIELD_DATE,
    FIELD_PERCENT_RATE,
    FIELD_DURATION,
    FIELD_QUANTITY,
    FIELD_PARTY_ROLE,
]

FIELD_REVIEW_FLAGS = {
    FIELD_AMOUNT: "CRITICAL_FIELD_AMOUNT_CHANGE",
    FIELD_DATE: "CRITICAL_FIELD_DATE_CHANGE",
    FIELD_PERCENT_RATE: "CRITICAL_FIELD_PERCENT_RATE_CHANGE",
    FIELD_DURATION: "CRITICAL_FIELD_DURATION_CHANGE",
    FIELD_QUANTITY: "CRITICAL_FIELD_QUANTITY_CHANGE",
    FIELD_PARTY_ROLE: "CRITICAL_FIELD_PARTY_ROLE_CHANGE",
}

AMOUNT_PATTERN = re.compile(
    r"(?:人民币|¥|￥)?\s*\d[\d,]*(?:\.\d+)?\s*(?:万|亿)?\s*元"
    r"|\d[\d,]*(?:\.\d+)?\s*(?:万元|亿元)"
)
DATE_PATTERN = re.compile(
    r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
)
PERCENT_RATE_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*[%‰]"
    r"|千分之[一二三四五六七八九十百千万零〇两\d]+"
)
DURATION_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:个工作日|工作日|日|天|个月|月|年)"
    r"|[一二三四五六七八九十百千万零〇两]+个工作日"
)
QUANTITY_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:台|套|个|项|批|份|件|人天)"
)
PARTY_ROLE_PATTERN = re.compile(r"甲方|乙方|买方|卖方|供应商|客户")
PARTY_ROLE_FRAGMENT_PATTERN = re.compile(r"甲|乙|买|卖|供应商|客户")
CHINESE_NUMERAL_CHARS = "一二三四五六七八九十百千万零〇两"
CHINESE_PERCENT_FRAGMENT_PATTERN = re.compile(rf"千分之[{CHINESE_NUMERAL_CHARS}\d]+")
CHINESE_DURATION_FRAGMENT_PATTERN = re.compile(rf"[{CHINESE_NUMERAL_CHARS}]+个工作日")
NUMERIC_FRAGMENT_PATTERN = re.compile(r"\d+(?:\.\d+)?")

FIELD_PATTERNS = {
    FIELD_AMOUNT: AMOUNT_PATTERN,
    FIELD_DATE: DATE_PATTERN,
    FIELD_PERCENT_RATE: PERCENT_RATE_PATTERN,
    FIELD_DURATION: DURATION_PATTERN,
    FIELD_QUANTITY: QUANTITY_PATTERN,
    FIELD_PARTY_ROLE: PARTY_ROLE_PATTERN,
}


@dataclass(frozen=True)
class FieldToken:
    field_type: str
    raw: str
    normalized: str


@dataclass(frozen=True)
class PatternMatch:
    raw: str
    start: int
    end: int


@dataclass(frozen=True)
class ChangeSpan:
    start: int
    end: int


def critical_field_diff_types(
    original_text: str,
    compare_text: str,
    original_snippet: str,
    compare_snippet: str,
) -> list[str]:
    if not _compact(original_snippet) and not _compact(compare_snippet):
        return []
    original_change_span, compare_change_span = _changed_spans(original_text, compare_text)
    field_types: list[str] = []
    for field_type in FIELD_ORDER:
        left_tokens = _changed_field_tokens(
            original_text,
            original_snippet,
            field_type,
            original_change_span,
        )
        right_tokens = _changed_field_tokens(
            compare_text,
            compare_snippet,
            field_type,
            compare_change_span,
        )
        if not left_tokens and not right_tokens:
            continue
        if _token_values(left_tokens) != _token_values(right_tokens):
            field_types.append(field_type)
    return field_types


def critical_field_review_flags(field_types: list[str]) -> list[str]:
    flags: list[str] = []
    for field_type in field_types:
        flag = FIELD_REVIEW_FLAGS.get(field_type)
        if flag and flag not in flags:
            flags.append(flag)
    if not flags:
        return []
    return [CRITICAL_FIELD_CHANGE, *flags]


def _changed_field_tokens(
    text: str,
    snippet: str,
    field_type: str,
    change_spans: list[ChangeSpan],
) -> list[FieldToken]:
    pattern = FIELD_PATTERNS[field_type]
    if field_type == FIELD_DURATION:
        text = _strip_dates(text)
        snippet = _strip_dates(snippet)
    tokens: list[FieldToken] = []
    for match in _pattern_matches(pattern, text):
        if _token_touches_change(match, text, snippet, change_spans):
            tokens.append(FieldToken(field_type, match.raw, _normalize_token(field_type, match.raw)))
    for match in _pattern_matches(pattern, snippet):
        tokens.append(FieldToken(field_type, match.raw, _normalize_token(field_type, match.raw)))
    return _dedupe_tokens(tokens)


def _pattern_matches(pattern: re.Pattern[str], text: str) -> list[PatternMatch]:
    return [PatternMatch(match.group(0), match.start(), match.end()) for match in pattern.finditer(text or "")]


def _token_touches_change(
    token_match: PatternMatch,
    text: str,
    snippet: str,
    change_spans: list[ChangeSpan],
) -> bool:
    token = _compact(token_match.raw)
    fragments = _snippet_candidate_fragments(snippet)
    if not token or not fragments:
        return False
    return any(
        _fragment_touches_token(fragment, token)
        and _fragment_overlaps_change_in_token(fragment, token_match, text, change_spans)
        for fragment in fragments
    )


def _changed_spans(original_text: str, compare_text: str) -> tuple[list[ChangeSpan], list[ChangeSpan]]:
    original = original_text or ""
    compare = compare_text or ""
    original_spans: list[ChangeSpan] = []
    compare_spans: list[ChangeSpan] = []
    matcher = difflib.SequenceMatcher(a=original, b=compare, autojunk=False)
    for tag, original_start, original_end, compare_start, compare_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        original_spans.append(ChangeSpan(original_start, original_end))
        compare_spans.append(ChangeSpan(compare_start, compare_end))
    return original_spans, compare_spans


def _snippet_candidate_fragments(snippet: str) -> list[str]:
    compact = _compact(snippet)
    if not compact:
        return []
    fragments = [compact]
    for pattern in (
        NUMERIC_FRAGMENT_PATTERN,
        PARTY_ROLE_FRAGMENT_PATTERN,
        CHINESE_PERCENT_FRAGMENT_PATTERN,
        CHINESE_DURATION_FRAGMENT_PATTERN,
    ):
        fragments.extend(match.group(0) for match in pattern.finditer(compact))
    return _dedupe_strings(fragments)


def _fragment_touches_token(fragment: str, token: str) -> bool:
    return bool(fragment) and (fragment == token or fragment in token)


def _fragment_overlaps_change_in_token(
    fragment: str,
    token_match: PatternMatch,
    text: str,
    change_spans: list[ChangeSpan],
) -> bool:
    for occurrence in re.finditer(re.escape(fragment), text or ""):
        if occurrence.start() < token_match.start or occurrence.end() > token_match.end:
            continue
        if _any_span_overlaps(occurrence.start(), occurrence.end(), change_spans):
            return True
    return fragment == _compact(token_match.raw) and _any_span_overlaps(
        token_match.start,
        token_match.end,
        change_spans,
    )


def _any_span_overlaps(
    token_start: int,
    token_end: int,
    change_spans: list[ChangeSpan],
) -> bool:
    return any(
        _spans_overlap(token_start, token_end, change_span.start, change_span.end)
        for change_span in change_spans
    )


def _spans_overlap(
    token_start: int,
    token_end: int,
    changed_start: int,
    changed_end: int,
) -> bool:
    if token_start < changed_end and changed_start < token_end:
        return True
    return changed_start == changed_end and token_start <= changed_start <= token_end


def _token_values(tokens: list[FieldToken]) -> list[str]:
    return [token.normalized for token in tokens]


def _dedupe_tokens(tokens: list[FieldToken]) -> list[FieldToken]:
    result: list[FieldToken] = []
    seen: set[tuple[str, str]] = set()
    for token in tokens:
        key = (token.field_type, token.normalized)
        if key in seen:
            continue
        seen.add(key)
        result.append(token)
    return result


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_token(field_type: str, raw: str) -> str:
    compact = _compact(raw)
    if field_type == FIELD_DATE:
        canonical = _canonical_date(compact)
        if canonical:
            return canonical
    if field_type == FIELD_AMOUNT:
        return compact.replace(",", "")
    return compact


def _canonical_date(compact: str) -> str:
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", compact)
    if match:
        return _date_key(match)
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", compact)
    if match:
        return _date_key(match)
    return ""


def _date_key(match: re.Match[str]) -> str:
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _strip_dates(text: str) -> str:
    return DATE_PATTERN.sub(lambda match: " " * len(match.group(0)), text or "")


def _compact(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", "", normalized)
