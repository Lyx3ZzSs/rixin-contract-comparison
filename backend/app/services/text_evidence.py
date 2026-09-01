from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import fitz

from app.models import BBox, DiffItem, Document, EvidenceBox, TextBlock
from app.services.extractors.ppocrv5 import PPOCRV5Extractor


EvidenceVerdict = Literal["EQUIVALENT", "DIFFERENT", "CONFLICT", "UNKNOWN"]

_PERCENT_TOKEN_RE = re.compile(r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>[%‰])")
_DATE_RE = re.compile(
    r"(?P<year>\d{4})\s*(?:年|[□▢■�./\-])\s*"
    r"(?P<month>\d{1,2})\s*(?:月|[□▢■�./\-])\s*"
    r"(?P<day>\d{1,2})\s*日?"
)
_BULLET_RE = re.compile(r"^(?:[•●▪◦·]|[-—–](?=\s))\s*")
_SCRIPT_CHARS_RE = re.compile(r"[₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹]")
_BLANK_UNDERSCORE_RE = re.compile(r"[_＿]{2,}")
_FORM_SLASH_RE = re.compile(r"(?:[_＿]*[/／][_＿]*)(?=(?:元|年|月|日|%|‰|个工作日|工作日))")
_SINGLE_NUMERIC_UNDERSCORE_RE = re.compile(r"[_＿](?=\d)")
_EMBEDDED_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})(?:\s*年\s*|\s+|[□▢■�./\-])"
    r"(?P<month>\d{1,2})(?:\s*月\s*|\s+|[□▢■�./\-])(?P<day>\d{1,2})\s*日?"
)
_FORMULA_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<base>[A-Z][A-Za-z]{1,2}?)(?P<index>[ijkn])?(?![A-Za-z0-9])"
)
_REVISION_LINE_RE = re.compile(
    r"^\s*(?:删除|插入)\[[\u4e00-\u9fff·]{2,8}\]\s*[:：]\s*.{0,32}\s*$"
    r"|^\s*设置格式\[[\u4e00-\u9fff·]{2,8}\](?:\s*[:：]\s*(?:突出显示|字体|字号|字形|颜色|下划线|加粗|倾斜|删除线)?)?\s*$"
)


@dataclass(frozen=True)
class TextEquivalenceDecision:
    equivalent: bool
    reason: str
    original_raw: str
    compare_raw: str
    original_normalized: str
    compare_normalized: str


@dataclass(frozen=True)
class EvidenceConsensusDecision:
    verdict: EvidenceVerdict
    reason: str
    sources: list[str] = field(default_factory=list)
    detail: dict[str, object] = field(default_factory=dict)


class TextEvidenceEquivalence:
    """Conservative, context-aware text equivalence without changing raw text."""

    def compare(self, original: str, compare: str, *, context: str = "") -> TextEquivalenceDecision:
        original_raw = original or ""
        compare_raw = compare or ""
        left = self._base(original_raw)
        right = self._base(compare_raw)

        if self._protected_percent_unit_change(left, right):
            return self._decision(False, "PROTECTED_PERCENT_UNIT_CHANGE", original_raw, compare_raw, left, right)
        if left == right:
            reason = (
                "FORMULA_SCRIPT_POSITION_EQUIVALENT"
                if _SCRIPT_CHARS_RE.search(original_raw) or _SCRIPT_CHARS_RE.search(compare_raw)
                else "UNICODE_WHITESPACE_EQUIVALENT"
            )
            return self._decision(True, reason, original_raw, compare_raw, left, right)

        revision_left = self._strip_revision_lines(original_raw)
        revision_right = self._strip_revision_lines(compare_raw)
        if revision_left == revision_right and (
            revision_left != left or revision_right != right
        ):
            return self._decision(
                True,
                "WORD_REVISION_ARTIFACT",
                original_raw,
                compare_raw,
                revision_left,
                revision_right,
            )

        date_layout = self._date_layout_equivalent(original_raw, compare_raw, context=context)
        if date_layout is not None:
            return self._decision(
                True,
                "DATE_SEPARATOR_GLYPH_NOISE",
                original_raw,
                compare_raw,
                date_layout[0],
                date_layout[1],
            )

        date_left = _DATE_RE.sub(self._canonical_date, left)
        date_right = _DATE_RE.sub(self._canonical_date, right)
        if date_left == date_right and (date_left != left or date_right != right):
            return self._decision(
                True,
                "DATE_SEPARATOR_GLYPH_NOISE",
                original_raw,
                compare_raw,
                date_left,
                date_right,
            )

        formula_left, formula_right = self._formula_pair(original_raw, compare_raw)
        if formula_left is not None and formula_left == formula_right:
            return self._decision(
                True,
                "FORMULA_SCRIPT_POSITION_EQUIVALENT",
                original_raw,
                compare_raw,
                formula_left,
                formula_right,
            )

        bullet_left = _BULLET_RE.sub("", left)
        bullet_right = _BULLET_RE.sub("", right)
        if bullet_left == bullet_right and (bullet_left != left or bullet_right != right):
            return self._decision(
                True,
                "BULLET_ATTACHMENT_NOISE",
                original_raw,
                compare_raw,
                bullet_left,
                bullet_right,
            )

        all_bullets_left = re.sub(r"[•●▪◦]", "", left)
        all_bullets_right = re.sub(r"[•●▪◦]", "", right)
        if all_bullets_left == all_bullets_right and (
            all_bullets_left != left or all_bullets_right != right
        ):
            return self._decision(
                True,
                "BULLET_ATTACHMENT_NOISE",
                original_raw,
                compare_raw,
                all_bullets_left,
                all_bullets_right,
            )

        slash_left = self._strip_standalone_slash_lines(original_raw)
        slash_right = self._strip_standalone_slash_lines(compare_raw)
        # A slash immediately before a form unit is the empty value itself.  It
        # must compare equal both to another slash and to an omitted empty cell;
        # keeping a synthetic token here made ``/元`` differ from ``元``.
        slash_left = _FORM_SLASH_RE.sub("", self._base(slash_left))
        slash_right = _FORM_SLASH_RE.sub("", self._base(slash_right))
        slash_left = _BLANK_UNDERSCORE_RE.sub("<blank>", slash_left)
        slash_right = _BLANK_UNDERSCORE_RE.sub("<blank>", slash_right)
        slash_left = _SINGLE_NUMERIC_UNDERSCORE_RE.sub("", slash_left)
        slash_right = _SINGLE_NUMERIC_UNDERSCORE_RE.sub("", slash_right)
        if slash_left == slash_right and (slash_left != left or slash_right != right):
            reason = (
                "FORM_SLASH_PLACEHOLDER_EQUIVALENT"
                if "/" in original_raw or "／" in original_raw or "/" in compare_raw or "／" in compare_raw
                else "FORM_UNDERSCORE_EQUIVALENT"
            )
            return self._decision(True, reason, original_raw, compare_raw, slash_left, slash_right)

        return self._decision(False, "NOT_EQUIVALENT", original_raw, compare_raw, left, right)

    @classmethod
    def _date_layout_equivalent(cls, left: str, right: str, *, context: str) -> tuple[str, str] | None:
        left_dates, left_normalized = cls._canonicalize_embedded_dates(left)
        right_dates, right_normalized = cls._canonicalize_embedded_dates(right)
        if left_dates and left_dates == right_dates:
            if len(left_dates) >= 2:
                left_normalized = left_normalized.replace("至", "")
                right_normalized = right_normalized.replace("至", "")
            left_key = cls._base(left_normalized)
            right_key = cls._base(right_normalized)
            if left_key == right_key:
                return left_key, right_key

        combined = f"{context}{left}{right}"
        if "日期" not in combined and not ("年" in combined and ("月" in combined or "日" in combined)):
            return None
        left_digits = re.findall(r"\d+", unicodedata.normalize("NFKC", left))
        right_digits = re.findall(r"\d+", unicodedata.normalize("NFKC", right))
        if not left_digits or left_digits != right_digits:
            return None
        left_residual = re.sub(r"[\d\s年月日□▢■�./\-:：]", "", unicodedata.normalize("NFKC", left))
        right_residual = re.sub(r"[\d\s年月日□▢■�./\-:：]", "", unicodedata.normalize("NFKC", right))
        if left_residual == right_residual:
            key = f"{left_residual}|{'-'.join(left_digits)}"
            return key, key
        return None

    @staticmethod
    def _canonicalize_embedded_dates(text: str) -> tuple[list[str], str]:
        dates: list[str] = []

        def replace(match: re.Match[str]) -> str:
            year, month, day = (int(match.group(name)) for name in ("year", "month", "day"))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return match.group(0)
            value = f"{year:04d}-{month:02d}-{day:02d}"
            dates.append(value)
            return f"<date:{value}>"

        return dates, _EMBEDDED_DATE_RE.sub(replace, unicodedata.normalize("NFKC", text or ""))

    @classmethod
    def _formula_pair(cls, left: str, right: str) -> tuple[str | None, str | None]:
        combined = f"{left}\n{right}"
        if not re.search(r"算法|公式|其中[:：]|[ijkn]时刻", combined, re.IGNORECASE):
            return None, None
        indexed_bases: set[str] = set()
        for text in (left, right):
            for match in _FORMULA_TOKEN_RE.finditer(unicodedata.normalize("NFKC", text or "")):
                if match.group("index"):
                    indexed_bases.add(match.group("base").casefold())
        if not indexed_bases:
            return None, None

        def normalize(text: str) -> str:
            def replace(match: re.Match[str]) -> str:
                base = match.group("base").casefold()
                return base if base in indexed_bases else match.group(0).casefold()

            return cls._base(_FORMULA_TOKEN_RE.sub(replace, unicodedata.normalize("NFKC", text or "")))

        return normalize(left), normalize(right)

    @staticmethod
    def _strip_standalone_slash_lines(text: str) -> str:
        return "\n".join(
            line for line in (text or "").splitlines() if re.fullmatch(r"\s*[/／]\s*", line) is None
        )

    @classmethod
    def _strip_revision_lines(cls, text: str) -> str:
        return cls._base("\n".join(line for line in (text or "").splitlines() if not _REVISION_LINE_RE.fullmatch(line)))

    @staticmethod
    def _base(text: str) -> str:
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).casefold()

    @staticmethod
    def _canonical_date(match: re.Match[str]) -> str:
        return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"

    @staticmethod
    def _protected_percent_unit_change(left: str, right: str) -> bool:
        left_tokens = [(item.group("number"), item.group("unit")) for item in _PERCENT_TOKEN_RE.finditer(left)]
        right_tokens = [(item.group("number"), item.group("unit")) for item in _PERCENT_TOKEN_RE.finditer(right)]
        return bool(
            left_tokens
            and right_tokens
            and len(left_tokens) == len(right_tokens)
            and any(a_number == b_number and a_unit != b_unit for (a_number, a_unit), (b_number, b_unit) in zip(left_tokens, right_tokens))
        )

    @staticmethod
    def _decision(
        equivalent: bool,
        reason: str,
        original_raw: str,
        compare_raw: str,
        original_normalized: str,
        compare_normalized: str,
    ) -> TextEquivalenceDecision:
        return TextEquivalenceDecision(
            equivalent=equivalent,
            reason=reason,
            original_raw=original_raw,
            compare_raw=compare_raw,
            original_normalized=original_normalized,
            compare_normalized=compare_normalized,
        )


class EvidenceConsensusEvaluator:
    """Adjudicate protected values only when real document evidence is available."""

    def __init__(self) -> None:
        self.text_equivalence = TextEvidenceEquivalence()
        self.ocr_extractor = PPOCRV5Extractor()

    def assess(
        self,
        diff: DiffItem,
        original_document: Document | None,
        compare_document: Document | None,
    ) -> EvidenceConsensusDecision:
        text_decision = self.text_equivalence.compare(
            diff.original_text,
            diff.compare_text,
            context=f"{diff.title} {diff.section_type}",
        )
        if text_decision.equivalent:
            return EvidenceConsensusDecision(
                verdict="EQUIVALENT",
                reason=text_decision.reason,
                sources=["original:normalized", "compare:normalized"],
                detail={
                    "original_raw": text_decision.original_raw,
                    "compare_raw": text_decision.compare_raw,
                    "original_normalized": text_decision.original_normalized,
                    "compare_normalized": text_decision.compare_normalized,
                },
            )
        identifier_field = self._critical_identifier_field(diff)
        if identifier_field:
            return self._assess_critical_identifier(
                diff,
                original_document,
                compare_document,
                field_key=identifier_field,
            )
        if text_decision.reason != "PROTECTED_PERCENT_UNIT_CHANGE":
            return EvidenceConsensusDecision(verdict="UNKNOWN", reason=text_decision.reason)

        original_unit = self._visual_percent_unit(original_document, diff.original_evidence)
        compare_unit = self._visual_percent_unit(compare_document, diff.compare_evidence)
        if original_unit is None or compare_unit is None:
            return EvidenceConsensusDecision(
                verdict="CONFLICT",
                reason="CRITICAL_NUMERIC_SOURCE_CONFLICT",
                sources=self._available_percent_sources(diff, original_unit, compare_unit),
            )
        sources = ["original:ocr", "original:visual", "compare:ocr", "compare:visual"]
        if original_unit == compare_unit:
            return EvidenceConsensusDecision(
                verdict="EQUIVALENT",
                reason="PERCENT_UNIT_VISUAL_CONSENSUS",
                sources=sources,
                detail={"original_visual": original_unit, "compare_visual": compare_unit},
            )
        return EvidenceConsensusDecision(
            verdict="DIFFERENT",
            reason="PERCENT_UNIT_VISUAL_DIFFERENCE_CONFIRMED",
            sources=sources,
            detail={"original_visual": original_unit, "compare_visual": compare_unit},
        )

    @staticmethod
    def _critical_identifier_field(diff: DiffItem) -> str:
        field_key = str(diff.match_score_details.get("field_key") or "")
        if field_key in {"credit_code", "account", "contract_number", "identity_number", "strict_identifier"}:
            return field_key
        title = re.sub(r"\s+", "", diff.title or "")
        for candidate, pattern in (
            ("credit_code", r"税号|统一社会信用代码|纳税人识别"),
            ("account", r"银行账号|账户|帐户|账号"),
            ("contract_number", r"合同编号|合同号"),
            ("identity_number", r"身份证|证件号|身份编号"),
        ):
            if re.search(pattern, title):
                return candidate
        return ""

    def _assess_critical_identifier(
        self,
        diff: DiffItem,
        original_document: Document | None,
        compare_document: Document | None,
        *,
        field_key: str,
    ) -> EvidenceConsensusDecision:
        original_candidates = self._identifier_candidates(
            original_document,
            diff.original_evidence,
            primary=diff.original_text,
            field_key=field_key,
        )
        compare_candidates = self._identifier_candidates(
            compare_document,
            diff.compare_evidence,
            primary=diff.compare_text,
            field_key=field_key,
        )
        original_values = {str(item["normalized_text"]) for item in original_candidates if item["normalized_text"]}
        compare_values = {str(item["normalized_text"]) for item in compare_candidates if item["normalized_text"]}
        conflict_type = self._identifier_conflict_type(original_values | compare_values)
        detail: dict[str, object] = {
            "field_type": field_key,
            "conflict_type": conflict_type,
            "original_candidates": original_candidates,
            "compare_candidates": compare_candidates,
        }
        sources = [
            *(f"original:{item['source']}" for item in original_candidates),
            *(f"compare:{item['source']}" for item in compare_candidates),
        ]
        original_source_count = len({str(item["source"]) for item in original_candidates})
        compare_source_count = len({str(item["source"]) for item in compare_candidates})
        if (
            original_values == compare_values
            and len(original_values) > 1
            and conflict_type == "AMBIGUOUS_GLYPH"
            and min(original_source_count, compare_source_count) >= 2
        ):
            detail["shared_candidates"] = sorted(original_values)
            return EvidenceConsensusDecision(
                verdict="EQUIVALENT",
                reason="CRITICAL_IDENTIFIER_SHARED_AMBIGUOUS_CANDIDATES",
                sources=list(dict.fromkeys(sources)),
                detail=detail,
            )
        if len(original_values) > 1 or len(compare_values) > 1:
            return EvidenceConsensusDecision(
                verdict="CONFLICT",
                reason="CRITICAL_IDENTIFIER_SOURCE_CONFLICT",
                sources=list(dict.fromkeys(sources)),
                detail=detail,
            )
        if not original_values or not compare_values:
            return EvidenceConsensusDecision(
                verdict="CONFLICT",
                reason="CRITICAL_IDENTIFIER_SOURCE_CONFLICT",
                sources=list(dict.fromkeys(sources)),
                detail=detail,
            )
        original_value = next(iter(original_values))
        compare_value = next(iter(compare_values))
        if min(original_source_count, compare_source_count) < 2:
            return EvidenceConsensusDecision(
                verdict="UNKNOWN",
                reason="CRITICAL_IDENTIFIER_SINGLE_SOURCE",
                sources=list(dict.fromkeys(sources)),
                detail=detail,
            )
        if original_value == compare_value:
            return EvidenceConsensusDecision(
                verdict="EQUIVALENT",
                reason="CRITICAL_IDENTIFIER_SOURCES_AGREE",
                sources=list(dict.fromkeys(sources)),
                detail=detail,
            )
        return EvidenceConsensusDecision(
            verdict="DIFFERENT",
            reason="CRITICAL_IDENTIFIER_DIFFERENCE_CONFIRMED",
            sources=list(dict.fromkeys(sources)),
            detail=detail,
        )

    def _identifier_candidates(
        self,
        document: Document | None,
        evidences: list[EvidenceBox],
        *,
        primary: str,
        field_key: str,
    ) -> list[dict[str, object]]:
        pages = {item.page_no for item in evidences if item.page_no > 0}
        candidates: list[dict[str, object]] = []
        if document is not None:
            for page in document.pages:
                if pages and page.page_no not in pages:
                    continue
                for block in page.blocks:
                    if evidences and not any(
                        evidence.page_no == block.page_no and self._bbox_overlap(block, evidence) >= 0.15
                        for evidence in evidences
                    ):
                        continue
                    for source in block.source_evidence:
                        value = self._identifier_value(source.raw_text, field_key)
                        if value:
                            candidates.append(
                                self._identifier_candidate(
                                    source=source.source,
                                    value=value,
                                    raw_text=source.raw_text,
                                    bbox=source.bbox,
                                    confidence=source.confidence,
                                )
                            )
            candidates.extend(self._native_identifier_candidates(document, evidences, field_key))
        if primary and not candidates:
            candidates.append(
                self._identifier_candidate(
                    source="selected_text",
                    value=primary,
                    raw_text=primary,
                    bbox=evidences[0].bbox if evidences else None,
                    confidence=evidences[0].confidence if evidences else None,
                )
            )
        unique: dict[tuple[str, str], dict[str, object]] = {}
        for item in candidates:
            unique.setdefault((str(item["source"]), str(item["normalized_text"])), item)
        return list(unique.values())

    def _native_identifier_candidates(
        self,
        document: Document,
        evidences: list[EvidenceBox],
        field_key: str,
    ) -> list[dict[str, object]]:
        if not document.path or not Path(document.path).is_file():
            return []
        result: list[dict[str, object]] = []
        try:
            pdf = fitz.open(document.path)
        except Exception:
            return []
        try:
            for evidence in evidences:
                if not 1 <= evidence.page_no <= len(pdf):
                    continue
                clip = fitz.Rect(
                    max(0.0, evidence.bbox.x0 - 120.0),
                    max(0.0, evidence.bbox.y0 - 8.0),
                    evidence.bbox.x1 + 16.0,
                    evidence.bbox.y1 + 8.0,
                )
                raw_text = pdf[evidence.page_no - 1].get_text("text", clip=clip)
                value = self._identifier_value(raw_text, field_key)
                if value:
                    result.append(
                        self._identifier_candidate(
                            source="native_text",
                            value=value,
                            raw_text=raw_text,
                            bbox=evidence.bbox,
                            confidence=1.0,
                        )
                    )
        finally:
            pdf.close()
        return result

    @staticmethod
    def _identifier_value(text: str, field_key: str) -> str:
        compact = unicodedata.normalize("NFKC", text or "")
        labels = {
            "credit_code": r"(?:统一社会信用代码|税号|纳税人识别(?:号)?)",
            "account": r"(?:银行账号|账号|帐户|账户)",
            "contract_number": r"(?:合同编号|合同号)",
            "identity_number": r"(?:身份证(?:号码?)?|证件号|身份编号)",
            "strict_identifier": r"(?:编号|代码)",
        }
        label = labels[field_key]
        match = re.search(rf"{label}\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-‐‑‒–—_ ]{{3,}})", compact)
        if match is not None:
            return match.group(1).strip()
        stripped = re.sub(r"\s+", "", compact)
        if re.fullmatch(r"[A-Za-z0-9\-‐‑‒–—_]{4,}", stripped):
            return stripped
        return ""

    @staticmethod
    def _identifier_candidate(
        *,
        source: str,
        value: str,
        raw_text: str,
        bbox: BBox | None,
        confidence: float | None,
    ) -> dict[str, object]:
        normalized = re.sub(r"[\s\-‐‑‒–—_]", "", unicodedata.normalize("NFKC", value or "")).upper()
        return {
            "source": source,
            "value": value,
            "raw_text": raw_text,
            "normalized_text": normalized,
            "bbox": bbox.model_dump() if bbox is not None else None,
            "confidence": confidence,
        }

    @staticmethod
    def _identifier_conflict_type(values: set[str]) -> str:
        if len(values) != 2:
            return "MULTI_SOURCE_VALUE_CONFLICT" if len(values) > 1 else ""
        left, right = sorted(values)
        if len(left) == len(right) and sum(a != b for a, b in zip(left, right)) == 1:
            pair = next((frozenset((a, b)) for a, b in zip(left, right) if a != b), frozenset())
            if pair in {frozenset(("O", "0")), frozenset(("I", "1")), frozenset(("L", "1")), frozenset(("S", "5"))}:
                return "AMBIGUOUS_GLYPH"
        return "MULTI_SOURCE_VALUE_CONFLICT"

    def _visual_percent_unit(
        self,
        document: Document | None,
        evidences: list[EvidenceBox],
    ) -> str | None:
        if document is None or not document.path or not Path(document.path).is_file():
            return None
        block = self._percent_block(document, evidences)
        if block is None:
            return None
        match = _PERCENT_TOKEN_RE.search(block.text or "")
        if match is None:
            return None
        unit_index = match.start("unit")
        indexes = {item.text_index for item in block.char_boxes if item.text_index is not None}
        if not {unit_index - 1, unit_index, unit_index + 1}.issubset(indexes):
            return None
        try:
            pdf = fitz.open(document.path)
        except Exception:
            return None
        try:
            return "‰" if self.ocr_extractor._visual_glyph_is_per_mille(pdf, block, unit_index) else "%"
        finally:
            pdf.close()

    @staticmethod
    def _percent_block(document: Document, evidences: list[EvidenceBox]) -> TextBlock | None:
        pages = {item.page_no for item in evidences}
        candidates = [
            block
            for page in document.pages
            if not pages or page.page_no in pages
            for block in page.blocks
            if _PERCENT_TOKEN_RE.search(block.text or "") and block.char_boxes
        ]
        if not candidates:
            return None
        if not evidences:
            return candidates[0]
        return max(
            candidates,
            key=lambda block: max(
                (
                    EvidenceConsensusEvaluator._bbox_overlap(block, evidence)
                    for evidence in evidences
                    if evidence.page_no == block.page_no
                ),
                default=0.0,
            ),
        )

    @staticmethod
    def _bbox_overlap(block: TextBlock, evidence: EvidenceBox) -> float:
        x0 = max(block.bbox.x0, evidence.bbox.x0)
        y0 = max(block.bbox.y0, evidence.bbox.y0)
        x1 = min(block.bbox.x1, evidence.bbox.x1)
        y1 = min(block.bbox.y1, evidence.bbox.y1)
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        area = max(1.0, (block.bbox.x1 - block.bbox.x0) * (block.bbox.y1 - block.bbox.y0))
        return inter / area

    @staticmethod
    def _available_percent_sources(
        diff: DiffItem,
        original_visual: str | None,
        compare_visual: str | None,
    ) -> list[str]:
        sources: list[str] = []
        if _PERCENT_TOKEN_RE.search(diff.original_text or ""):
            sources.append("original:ocr")
        if original_visual is not None:
            sources.append("original:visual")
        if _PERCENT_TOKEN_RE.search(diff.compare_text or ""):
            sources.append("compare:ocr")
        if compare_visual is not None:
            sources.append("compare:visual")
        return sources
