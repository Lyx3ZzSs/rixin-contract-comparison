from __future__ import annotations

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


def critical_field_diff_types(
    original_text: str,
    compare_text: str,
    original_snippet: str,
    compare_snippet: str,
) -> list[str]:
    if not _compact(original_snippet) or not _compact(compare_snippet):
        return []
    field_types: list[str] = []
    for field_type in FIELD_ORDER:
        left_tokens = _changed_field_tokens(original_text, original_snippet, field_type)
        right_tokens = _changed_field_tokens(compare_text, compare_snippet, field_type)
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


def _changed_field_tokens(text: str, snippet: str, field_type: str) -> list[FieldToken]:
    pattern = FIELD_PATTERNS[field_type]
    if field_type == FIELD_DURATION:
        text = _strip_dates(text)
        snippet = _strip_dates(snippet)
    tokens: list[FieldToken] = []
    for raw in _pattern_values(pattern, text):
        if _token_touches_change(raw, snippet):
            tokens.append(FieldToken(field_type, raw, _normalize_token(field_type, raw)))
    for raw in _pattern_values(pattern, snippet):
        tokens.append(FieldToken(field_type, raw, _normalize_token(field_type, raw)))
    return _dedupe_tokens(tokens)


def _pattern_values(pattern: re.Pattern[str], text: str) -> list[str]:
    return [match.group(0) for match in pattern.finditer(text or "")]


def _token_touches_change(raw_token: str, snippet: str) -> bool:
    token = _compact(raw_token)
    changed = _compact(snippet)
    if not token or not changed:
        return False
    if token in changed or changed in token:
        return True
    token_numbers = set(re.findall(r"\d+(?:\.\d+)?", token))
    snippet_numbers = set(re.findall(r"\d+(?:\.\d+)?", changed))
    return bool(token_numbers and snippet_numbers and token_numbers.intersection(snippet_numbers))


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
    return DATE_PATTERN.sub("", text or "")


def _compact(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", "", normalized)
