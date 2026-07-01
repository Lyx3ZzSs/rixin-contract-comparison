from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.models import Clause, DiffItem, Document

STRUCTURAL_RISK_FLAGS = {
    "POSSIBLE_SPLIT_DRIFT",
    "LOW_CONFIDENCE_MATCH",
    "LOW_COVERAGE_CLAUSE_KEY_MATCH",
    "PARAGRAPH_MERGED",
    "POSSIBLE_BOUNDARY_DRIFT",
    "READING_ORDER_REPAIRED",
    "READING_ORDER_RISK",
}

_APPENDIX_HEADING_PATTERN = re.compile(r"^\s*(附件\s*[一二三四五六七八九十百\d]+)\s*[:：.．、]?\s*$")
_APPENDIX_HEADING_LINE_PATTERN = re.compile(r"^\s*(附件\s*[一二三四五六七八九十百\d]+)\s*(?:[:：.．、]|$)")
_COMPACT_PUNCTUATION_PATTERN = re.compile(r"[\s，。；：、”“‘’（）()\[\]【】《》!?:;\"'.,．]+")
_COVERAGE_SEPARATOR_PATTERN = re.compile(r"[-－—_]+")
_FIELD_LABEL_PATTERN = re.compile(
    r"(?P<label>联系人|电话|传真|邮箱|电子邮箱|Email|E-mail|统一社会信用代码)\s*[:：]?",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\d{3,4}-\d{7,8}|1[3-9]\d{9})(?!\d)")
_CREDIT_CODE_PATTERN = re.compile(r"统一社会信用代码\s*[:：]?\s*(?P<value>[0-9A-ZＸX\s\-－]{15,32})", re.IGNORECASE)
_CONTACT_VALUE_PATTERN = re.compile(r"\s*(?P<value>[^\s，。；;、,]+)")
_CREDIT_CODE_VALUE_PATTERN = re.compile(r"\s*(?P<value>[0-9A-ZＸX\s\-－]{8,32})", re.IGNORECASE)


@dataclass(frozen=True)
class _FieldToken:
    label: str
    kind: str
    value: str
    normalized_value: str
    start: int
    end: int


@dataclass(frozen=True)
class CoverageFragment:
    kind: str
    text: str
    normalized: str


@dataclass(frozen=True)
class CoverageSequence:
    text: str
    normalized: str


@dataclass(frozen=True)
class _ClauseIndex:
    clauses: tuple[Clause, ...]
    positions: dict[str, int]

    @classmethod
    def from_clauses(cls, clauses: list[Clause]) -> _ClauseIndex:
        sorted_clauses = tuple(sorted(clauses, key=lambda item: (item.order_index, item.clause_id)))
        return cls(
            clauses=sorted_clauses,
            positions={clause.clause_id: index for index, clause in enumerate(sorted_clauses)},
        )

    def window_text(self, clause_id: str | None, radius: int = 2) -> str:
        if not clause_id or clause_id not in self.positions:
            return ""
        position = self.positions[clause_id]
        start = max(0, position - radius)
        end = min(len(self.clauses), position + radius + 1)
        return "\n".join(clause.text for clause in self.clauses[start:end] if clause.text)


@dataclass
class BoundaryCoverageContext:
    original_clauses: list[Clause] = field(default_factory=list)
    compare_clauses: list[Clause] = field(default_factory=list)
    original_document: Document | None = None
    compare_document: Document | None = None
    _original_index: _ClauseIndex | None = field(default=None, init=False, repr=False)
    _compare_index: _ClauseIndex | None = field(default=None, init=False, repr=False)

    @property
    def original_index(self) -> _ClauseIndex:
        if self._original_index is None:
            self._original_index = _ClauseIndex.from_clauses(self.original_clauses)
        return self._original_index

    @property
    def compare_index(self) -> _ClauseIndex:
        if self._compare_index is None:
            self._compare_index = _ClauseIndex.from_clauses(self.compare_clauses)
        return self._compare_index


@dataclass
class BoundaryCoverageDecision:
    action: str
    diff_id: str
    detail: dict[str, Any] = field(default_factory=dict)


class ClauseBoundaryCoverageFilter:
    def filter(
        self,
        diffs: list[DiffItem],
        context: BoundaryCoverageContext,
    ) -> tuple[list[DiffItem], list[BoundaryCoverageDecision]]:
        kept: list[DiffItem] = []
        decisions: list[BoundaryCoverageDecision] = []
        for diff in diffs:
            suppression_reason = self._suppression_reason(diff, context)
            if suppression_reason:
                decisions.append(
                    BoundaryCoverageDecision(
                        action="suppressed_by_neighbor_clause_coverage",
                        diff_id=diff.diff_id,
                        detail={"reason": suppression_reason},
                    )
                )
                continue
            kept.append(diff)
        return kept, decisions

    def _suppression_reason(self, diff: DiffItem, context: BoundaryCoverageContext) -> str:
        if self._short_appendix_heading_covered(diff, context):
            return "short_appendix_heading_covered"
        if self._changed_fragments_covered_by_neighbor_clauses(diff, context):
            return "changed_fragments_covered_by_neighbor_clauses"
        return ""

    def _short_appendix_heading_covered(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type not in {"ADD", "DELETE"}:
            return False
        if diff.section_type != "appendix":
            return False

        changed = (
            (diff.original_text or diff.original_snippet)
            if diff.diff_type == "DELETE"
            else (diff.compare_text or diff.compare_snippet)
        )
        heading_key = appendix_heading_key(changed)
        if not heading_key:
            return False

        opposite_document = context.compare_document if diff.diff_type == "DELETE" else context.original_document
        if opposite_document is None:
            return False

        evidence_pages = self._evidence_pages(diff)
        if not evidence_pages:
            return False

        candidate_pages = {page_no + offset for page_no in evidence_pages for offset in (-1, 0, 1)}
        for page in opposite_document.pages:
            if page.page_no not in candidate_pages:
                continue
            page_text = "\n".join(block.text for block in page.blocks)
            if contains_appendix_heading(page_text, heading_key):
                return True
        return False

    @staticmethod
    def _evidence_pages(diff: DiffItem) -> set[int]:
        if diff.diff_type == "DELETE":
            return {evidence.page_no for evidence in diff.original_evidence}
        return {evidence.page_no for evidence in diff.compare_evidence}

    def _changed_fragments_covered_by_neighbor_clauses(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if not _eligible_structural_clause_diff(diff):
            return False

        original_window = context.original_index.window_text(diff.original_clause_id)
        compare_window = context.compare_index.window_text(diff.compare_clause_id)
        if not original_window or not compare_window:
            return False

        fragments = _dedupe_fragments(
            [
                *protected_fragments(diff.original_snippet),
                *protected_fragments(diff.compare_snippet),
            ]
        )
        if not fragments:
            return False
        if not _snippets_contain_only_protected_boundary_fields(diff, original_window, compare_window):
            return False

        original_coverage = normalize_for_coverage(original_window)
        compare_coverage = normalize_for_coverage(compare_window)
        if not all(
            fragment.normalized
            and fragment.normalized in original_coverage
            and fragment.normalized in compare_coverage
            for fragment in fragments
        ):
            return False

        required_sequences = _dedupe_sequences(
            [
                *contact_field_sequences(diff.original_snippet),
                *contact_field_sequences(diff.compare_snippet),
            ]
        )
        if not required_sequences:
            if not _bare_phone_fragments_safe(diff):
                return False
            return True

        original_sequences = contact_field_coverage_sequences(original_window)
        compare_sequences = contact_field_coverage_sequences(compare_window)
        return all(
            sequence.normalized in original_sequences and sequence.normalized in compare_sequences
            for sequence in required_sequences
        )


def compact_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return _COMPACT_PUNCTUATION_PATTERN.sub("", normalized).lower()


def normalize_for_coverage(text: str) -> str:
    return _COVERAGE_SEPARATOR_PATTERN.sub("", compact_text(text))


def protected_fragments(text: str) -> list[CoverageFragment]:
    fragments: list[CoverageFragment] = []
    fragments.extend(
        CoverageFragment("contact_field", value, normalize_for_coverage(value)) for value in contact_field_values(text)
    )
    fragments.extend(CoverageFragment("email", value, normalize_email(value)) for value in emails(text))
    fragments.extend(CoverageFragment("phone", value, normalize_phone(value)) for value in phones(text))
    fragments.extend(
        CoverageFragment("credit_code", value, normalize_credit_code(value)) for value in credit_code_candidates(text)
    )
    return [fragment for fragment in fragments if fragment.normalized]


def contact_field_values(text: str) -> list[str]:
    return [token.value for token in _field_tokens(text) if token.kind in {"contact", "phone", "fax"}]


def contact_field_sequences(text: str) -> list[CoverageSequence]:
    tokens = [token for token in _field_tokens(text) if token.kind in {"contact", "phone", "fax"}]
    if not tokens:
        return []
    parts = [f"{token.label}:{token.value}" for token in tokens]
    sequence_text = "".join(parts)
    return [CoverageSequence(sequence_text, normalize_for_coverage(sequence_text))]


def contact_field_coverage_sequences(text: str) -> set[str]:
    tokens = [token for token in _field_tokens(text) if token.kind in {"contact", "phone", "fax"}]
    sequences = {sequence.normalized for sequence in _contiguous_contact_field_sequences(tokens)}
    sequences.update(_inferred_contact_field_sequences(tokens))
    return {sequence for sequence in sequences if sequence}


def emails(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    return [match.group(0) for match in _EMAIL_PATTERN.finditer(normalized)]


def phones(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    return [match.group(0) for match in _PHONE_PATTERN.finditer(normalized)]


def _bare_phone_fragments_safe(diff: DiffItem) -> bool:
    original_phones = [normalize_phone(value) for value in phones(diff.original_snippet)]
    compare_phones = [normalize_phone(value) for value in phones(diff.compare_snippet)]
    if not original_phones and not compare_phones:
        return True
    return set(original_phones) == set(compare_phones)


def credit_code_candidates(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    candidates: list[str] = []
    for match in _CREDIT_CODE_PATTERN.finditer(normalized):
        candidate = normalize_credit_code(match.group("value"))
        if len(candidate) == 18:
            candidates.append(candidate)
    return candidates


def normalize_email(text: str) -> str:
    return normalize_for_coverage(text)


def normalize_phone(text: str) -> str:
    return normalize_for_coverage(text)


def normalize_credit_code(text: str) -> str:
    return normalize_for_coverage(text).upper()


def _field_tokens(text: str) -> list[_FieldToken]:
    normalized = unicodedata.normalize("NFKC", text or "")
    labels = list(_FIELD_LABEL_PATTERN.finditer(normalized))
    tokens: list[_FieldToken] = []
    for index, label_match in enumerate(labels):
        label = label_match.group("label")
        kind = _field_kind(label)
        next_label_start = labels[index + 1].start() if index + 1 < len(labels) else len(normalized)
        value_region = normalized[label_match.end() : next_label_start]
        value_match = _field_value_match(kind, value_region)
        if value_match is None:
            continue
        value_group: str | int = "value" if "value" in value_match.re.groupindex else 0
        value = value_match.group(value_group).strip()
        if not value:
            continue
        value_end = label_match.end() + value_match.end(value_group)
        normalized_value = _normalize_field_value(kind, value)
        tokens.append(
            _FieldToken(
                label=label,
                kind=kind,
                value=value,
                normalized_value=normalized_value,
                start=label_match.start(),
                end=value_end,
            )
        )
    return tokens


def _field_kind(label: str) -> str:
    normalized = unicodedata.normalize("NFKC", label).lower()
    if normalized == "联系人":
        return "contact"
    if normalized == "电话":
        return "phone"
    if normalized == "传真":
        return "fax"
    if normalized in {"邮箱", "电子邮箱", "email", "e-mail"}:
        return "email"
    return "credit_code"


def _field_value_match(kind: str, value_region: str) -> re.Match[str] | None:
    if kind == "contact":
        return _CONTACT_VALUE_PATTERN.match(value_region)
    if kind in {"phone", "fax"}:
        return _PHONE_PATTERN.search(value_region)
    if kind == "email":
        return _EMAIL_PATTERN.search(value_region)
    return _CREDIT_CODE_VALUE_PATTERN.match(value_region)


def _normalize_field_value(kind: str, value: str) -> str:
    if kind == "email":
        return normalize_email(value)
    if kind in {"phone", "fax"}:
        return normalize_phone(value)
    if kind == "credit_code":
        return normalize_credit_code(value)
    return normalize_for_coverage(value)


def _contiguous_contact_field_sequences(tokens: list[_FieldToken]) -> list[CoverageSequence]:
    sequences: list[CoverageSequence] = []
    if not tokens:
        return sequences

    current: list[_FieldToken] = []
    for token in tokens:
        if token.kind == "contact":
            if current:
                sequences.append(_sequence_from_tokens(current))
            current = [token]
            continue
        if current:
            current.append(token)
        else:
            sequences.append(_sequence_from_tokens([token]))
    if current:
        sequences.append(_sequence_from_tokens(current))
    return sequences


def _inferred_contact_field_sequences(tokens: list[_FieldToken]) -> set[str]:
    contacts = [token for token in tokens if token.kind == "contact"]
    phones = [token for token in tokens if token.kind == "phone"]
    faxes = [token for token in tokens if token.kind == "fax"]
    if not contacts:
        return set()

    sequences: set[str] = set()
    for index, contact in enumerate(contacts):
        group = [contact]
        if index < len(phones):
            group.append(phones[index])
        if index < len(faxes):
            group.append(faxes[index])
        if len(group) > 1:
            sequences.add(_sequence_from_tokens(group).normalized)
    return sequences


def _sequence_from_tokens(tokens: list[_FieldToken]) -> CoverageSequence:
    text = "".join(f"{token.label}:{token.value}" for token in tokens)
    return CoverageSequence(text, normalize_for_coverage(text))


def _snippets_contain_only_protected_boundary_fields(
    diff: DiffItem,
    original_window: str,
    compare_window: str,
) -> bool:
    return _credit_code_tokens_safe(
        diff.original_snippet,
        diff.compare_snippet,
        original_window,
        compare_window,
    ) and (
        _contains_only_protected_boundary_fields(diff.original_snippet)
        and _contains_only_protected_boundary_fields(diff.compare_snippet)
    )


def _contains_only_protected_boundary_fields(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "")
    if not normalized:
        return True

    mask = [False] * len(normalized)
    for token in _field_tokens(normalized):
        _mark_span(mask, token.start, token.end)
    for pattern in (_EMAIL_PATTERN, _PHONE_PATTERN):
        for match in pattern.finditer(normalized):
            _mark_span(mask, match.start(), match.end())

    residual = "".join(" " if masked else char for char, masked in zip(normalized, mask, strict=True))
    return not compact_text(residual)


def _mark_span(mask: list[bool], start: int, end: int) -> None:
    for index in range(max(0, start), min(len(mask), end)):
        mask[index] = True


def _credit_code_tokens_safe(
    original_snippet: str,
    compare_snippet: str,
    original_window: str,
    compare_window: str,
) -> bool:
    original_values = _credit_code_token_values(original_snippet)
    compare_values = _credit_code_token_values(compare_snippet)
    all_values = [*original_values, *compare_values]
    if not any(len(value) != 18 for value in all_values):
        return True
    if original_values and original_values == compare_values:
        return True

    original_window_values = _credit_code_token_values(original_window)
    compare_window_values = _credit_code_token_values(compare_window)
    if not original_window_values or not compare_window_values:
        return False
    return all(
        _prefix_compatible_with_any(value, original_window_values)
        and _prefix_compatible_with_any(value, compare_window_values)
        for value in all_values
    )


def _credit_code_token_values(text: str) -> list[str]:
    return [token.normalized_value for token in _field_tokens(text) if token.kind == "credit_code"]


def _prefix_compatible_with_any(value: str, candidates: list[str]) -> bool:
    return any(candidate.startswith(value) or value.startswith(candidate) for candidate in candidates)


def _eligible_structural_clause_diff(diff: DiffItem) -> bool:
    if diff.source_type != "clause":
        return False
    risk_flags = set(diff.structural_flags) | set(diff.review_flags)
    return bool(risk_flags.intersection(STRUCTURAL_RISK_FLAGS))


def _dedupe_fragments(fragments: list[CoverageFragment]) -> list[CoverageFragment]:
    deduped: list[CoverageFragment] = []
    seen: set[tuple[str, str]] = set()
    for fragment in fragments:
        key = (fragment.kind, fragment.normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(fragment)
    return deduped


def _dedupe_sequences(sequences: list[CoverageSequence]) -> list[CoverageSequence]:
    deduped: list[CoverageSequence] = []
    seen: set[str] = set()
    for sequence in sequences:
        if not sequence.normalized or sequence.normalized in seen:
            continue
        seen.add(sequence.normalized)
        deduped.append(sequence)
    return deduped


def appendix_heading_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    if len(compact_text(normalized)) > 8:
        return ""
    match = _APPENDIX_HEADING_PATTERN.fullmatch(normalized)
    if not match:
        return ""
    return compact_text(match.group(1))


def contains_appendix_heading(text: str, heading_key: str) -> bool:
    if not heading_key:
        return False
    normalized = unicodedata.normalize("NFKC", text or "").replace("\f", "\n")
    for line in normalized.splitlines():
        match = _APPENDIX_HEADING_LINE_PATTERN.match(line)
        if match and compact_text(match.group(1)) == heading_key:
            return True
    return False
