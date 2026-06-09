from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field as dataclass_field
from difflib import SequenceMatcher

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - fallback for minimal environments
    fuzz = None

from app.models import BBox, CharBox, DiffItem, Document, EvidenceBox, TextBlock, TextRange
from app.services.diff.range_refiner import changed_snippets
from app.utils.id_utils import generate_diff_id


FIELD_TERMS = (
    "甲方",
    "乙方",
    "买方",
    "卖方",
    "供方",
    "需方",
    "授权代表签字",
    "授权代表",
    "法定代表人",
    "法人代表或授权委托人",
    "法人代表",
    "纳税人识别号",
    "纳税人识别",
    "地址",
    "电话",
    "开户行",
    "账号",
    "银行行号",
    "邮编",
    "日期",
)
_SORTED_FIELD_TERMS = tuple(sorted(FIELD_TERMS, key=len, reverse=True))
FIELD_ALIASES = {
    "授权代表签字": "授权代表",
    "法人代表或授权委托人": "法人代表",
    "纳税人识别号": "纳税人识别",
}
IDENTITY_LABELS = {"甲方", "乙方", "买方", "卖方", "供方", "需方"}
NOISE_TEXTS = {"站股", "心"}
PUNCT_PATTERN = re.compile(r"[\s:：，。；;、()\[\]（）【】]+")
DATE_PATTERN = re.compile(r"^(?:19|20)\d{2}[./年-]\d{1,2}(?:[./月-]\d{1,2}日?)?$")
NAME_PATTERN = re.compile(r"^[\u4e00-\u9fff]{2,5}$")
VALUE_TOKEN_PATTERN = re.compile(r"(?:19|20)\d{2}[./年-]\d{1,2}(?:[./月-]\d{1,2}日?)?|[0-9][0-9A-Za-z./_-]{3,}")
UNLABELED_LABEL = "未标注签署文本"
STRONG_VALUE_LABELS = {"账号", "电话", "银行行号", "日期", "纳税人识别", "邮编"}


@dataclass
class SignatureValuePart:
    text: str
    block: TextBlock
    block_start: int | None = None
    block_end: int | None = None


@dataclass(frozen=True)
class SignatureParsedPart:
    label: str
    value: str | None
    column: str
    sort_x: float
    block_start: int | None = None
    block_end: int | None = None


@dataclass
class SignatureField:
    page_no: int
    column: str
    label: str
    sort_y: float
    sort_x: float
    text_parts: list[str] = dataclass_field(default_factory=list)
    blocks: list[TextBlock] = dataclass_field(default_factory=list)
    value_parts: list[SignatureValuePart] = dataclass_field(default_factory=list)

    @property
    def text(self) -> str:
        parts = [part.text for part in self.value_parts] if self.value_parts else self.text_parts
        return " ".join(part for part in parts if part).strip()

    @property
    def has_value(self) -> bool:
        return bool(PUNCT_PATTERN.sub("", self.text))

    @property
    def is_empty_labeled_field(self) -> bool:
        label, _, _ = self.label.partition("#")
        return label != UNLABELED_LABEL and not self.has_value

    def add_value(
        self,
        text: str,
        block: TextBlock,
        block_start: int | None = None,
        block_end: int | None = None,
    ) -> None:
        if text:
            self.text_parts.append(text)
            self.value_parts.append(SignatureValuePart(text, block, block_start, block_end))
        if block not in self.blocks:
            self.blocks.append(block)


@dataclass(frozen=True)
class SignatureMatch:
    original: SignatureField | None
    compare: SignatureField | None
    score: float | None = None
    method: str = ""
    details: dict[str, float] = dataclass_field(default_factory=dict)


class SignatureComparator:
    def build_diffs(
        self,
        original: Document,
        compare: Document,
        start_index: int = 1,
    ) -> list[DiffItem]:
        original_fields = self._collect_fields(original)
        compare_fields = self._collect_fields(compare)
        if not original_fields and not compare_fields:
            return []

        diffs: list[DiffItem] = []
        next_index = start_index
        for match in self._match_fields(original_fields, compare_fields):
            diff = self._build_diff(match, next_index)
            if diff is None:
                continue
            diffs.append(diff)
            next_index += 1
        return diffs

    def _collect_fields(self, document: Document) -> list[SignatureField]:
        fields: list[SignatureField] = []
        for page in document.pages:
            blocks = [
                block
                for block in sorted(page.blocks, key=lambda item: (item.bbox.y0, item.bbox.x0, item.block_id))
                if (block.block_role or "").lower() == "signature_field"
                and (block.block_type or "").lower() not in {"seal", "stamp", "signature", "image", "figure"}
                and (block.text or "").strip()
            ]
            if not blocks:
                continue
            split_x = self._column_split(blocks, page.width)
            page_fields = self._labeled_fields(blocks, split_x)
            self._attach_unlabeled_blocks(blocks, page_fields, split_x)
            fields.extend(page_fields)
        return self._with_occurrence_order(fields)

    def _labeled_fields(self, blocks: list[TextBlock], split_x: float) -> list[SignatureField]:
        fields: list[SignatureField] = []
        for block in blocks:
            parsed_parts = self._parse_labeled_field_parts(block, split_x)
            if not parsed_parts:
                continue
            for part in parsed_parts:
                field = SignatureField(
                    page_no=block.page_no,
                    column=part.column,
                    label=part.label,
                    sort_y=block.bbox.y0,
                    sort_x=part.sort_x,
                )
                field.add_value(part.value or "", block, part.block_start, part.block_end)
                if not field.blocks:
                    field.blocks.append(block)
                fields.append(field)
        return fields

    def _parse_labeled_field_parts(
        self,
        block: TextBlock,
        split_x: float,
    ) -> list[SignatureParsedPart]:
        repeated = self._split_repeated_label_block(block, split_x)
        if repeated:
            return repeated
        multi = self._split_multi_label_block(block, split_x)
        if multi:
            return multi
        label, value, block_start, block_end = self._parse_labeled_text_with_span(block.text)
        if not label:
            return []
        return [
            SignatureParsedPart(
                label=label,
                value=value,
                column=self._column(block.bbox, split_x),
                sort_x=block.bbox.x0,
                block_start=block_start,
                block_end=block_end,
            )
        ]

    def _split_multi_label_block(
        self,
        block: TextBlock,
        split_x: float,
    ) -> list[SignatureParsedPart]:
        compact, index_map = self._compact_with_index_map(block.text)
        positions = self._find_label_positions(compact)
        if len(positions) < 2:
            return []
        identity_order = 0
        parts: list[SignatureParsedPart] = []
        for i, (start, term) in enumerate(positions):
            label = FIELD_ALIASES.get(term, term)
            value_start = start + len(term)
            while value_start < len(compact) and compact[value_start] in ":：":
                value_start += 1
            value_end = positions[i + 1][0] if i + 1 < len(positions) else len(compact)
            value = self._clean_value(compact[value_start:value_end])
            if term in IDENTITY_LABELS:
                column = "left" if identity_order == 0 else "right"
                identity_order += 1
            else:
                column = self._column(block.bbox, split_x)
            block_start = index_map[start] if start < len(index_map) else None
            block_end = (
                index_map[value_end - 1] + 1
                if value_start <= value_end and value_end - 1 < len(index_map)
                else None
            )
            parts.append(SignatureParsedPart(
                label=label,
                value=value,
                column=column,
                sort_x=block.bbox.x0,
                block_start=block_start,
                block_end=block_end,
            ))
        return parts

    def _find_label_positions(self, compact: str) -> list[tuple[int, str]]:
        positions: list[tuple[int, str]] = []
        for term in _SORTED_FIELD_TERMS:
            for m in re.finditer(re.escape(term), compact):
                start = m.start()
                if any(s <= start < s + len(t) for s, t in positions):
                    continue
                positions.append((start, term))
        positions.sort()
        return positions

    def _split_repeated_label_block(
        self,
        block: TextBlock,
        split_x: float,
    ) -> list[SignatureParsedPart]:
        compact, index_map = self._compact_with_index_map(block.text)
        parts = self._split_repeated_label_text(compact, index_map, "地址")
        if len(parts) < 2:
            return []
        left_value, left_start, left_end = parts[0]
        right_value, right_start, right_end = parts[1]
        return [
            SignatureParsedPart("地址", left_value, "left", block.bbox.x0, left_start, left_end),
            SignatureParsedPart(
                "地址",
                right_value,
                "right",
                max(split_x + 1.0, (block.bbox.x0 + block.bbox.x1) / 2),
                right_start,
                right_end,
            ),
        ]

    def _split_repeated_label_text(
        self,
        compact: str,
        index_map: list[int],
        label: str,
    ) -> list[tuple[str, int | None, int | None]]:
        marker_pattern = rf"{re.escape(label)}[:：]"
        matches = list(re.finditer(marker_pattern, compact))
        if len(matches) < 2:
            return []
        values: list[tuple[str, int | None, int | None]] = []
        for index, match in enumerate(matches[:2]):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(compact)
            values.append((
                self._clean_value(compact[start:end]),
                index_map[start] if start < len(index_map) else None,
                index_map[end - 1] + 1 if start < end and end - 1 < len(index_map) else None,
            ))
        return values

    def _attach_unlabeled_blocks(
        self,
        blocks: list[TextBlock],
        fields: list[SignatureField],
        split_x: float,
    ) -> None:
        labeled_ids = {block.block_id for field in fields for block in field.blocks}
        for block in blocks:
            if block.block_id in labeled_ids:
                continue
            text = self._clean_value(block.text)
            if not text or self._is_seal_fragment(text):
                continue
            nearest = self._nearest_attachable_field(block, fields, split_x)
            if nearest is not None:
                nearest.add_value(text, block, 0, len(block.text or ""))
                continue
            if not self._is_standalone_signature_value(text, block, split_x):
                continue
            field = SignatureField(
                page_no=block.page_no,
                column=self._column(block.bbox, split_x),
                label=UNLABELED_LABEL,
                sort_y=block.bbox.y0,
                sort_x=block.bbox.x0,
            )
            field.add_value(text, block, 0, len(block.text or ""))
            fields.append(field)

    def _nearest_attachable_field(
        self,
        block: TextBlock,
        fields: list[SignatureField],
        split_x: float,
    ) -> SignatureField | None:
        column = self._column(block.bbox, split_x)
        y_mid = self._mid_y(block.bbox)
        candidates = [
            field
            for field in fields
            if field.page_no == block.page_no
            and field.column == column
            and abs(self._field_mid_y(field) - y_mid) <= 32.0
        ]
        if not candidates:
            return None
        text = self._clean_value(block.text)
        if re.fullmatch(r"\d+号", text):
            address_candidates = [field for field in candidates if self._base_label(field) == "地址"]
            if address_candidates:
                return min(address_candidates, key=lambda field: abs(self._field_mid_y(field) - y_mid))
        if self._normalized(text) == "签字":
            signer_candidates = [
                field
                for field in candidates
                if self._base_label(field) in {"法人代表", "授权代表", "法定代表人"}
            ]
            if signer_candidates:
                return min(signer_candidates, key=lambda field: abs(self._field_mid_y(field) - y_mid))
        if NAME_PATTERN.fullmatch(text):
            candidates = [field for field in candidates if self._base_label(field) not in IDENTITY_LABELS]
            if not candidates:
                return None
        return min(candidates, key=lambda field: abs(self._field_mid_y(field) - y_mid))

    def _match_fields(
        self,
        original: list[SignatureField],
        compare: list[SignatureField],
    ) -> list[SignatureMatch]:
        groups: dict[tuple[int, str], tuple[list[SignatureField], list[SignatureField]]] = {}
        for field in original:
            groups.setdefault(self._field_group_key(field), ([], []))[0].append(field)
        for field in compare:
            groups.setdefault(self._field_group_key(field), ([], []))[1].append(field)

        matches: list[SignatureMatch] = []
        for key in sorted(groups):
            orig_group, comp_group = groups[key]
            group_matches = self._match_field_group(orig_group, comp_group)
            matches.extend(self._drop_duplicate_unmatched(group_matches))
        return matches

    def _match_field_group(
        self,
        original: list[SignatureField],
        compare: list[SignatureField],
    ) -> list[SignatureMatch]:
        candidates: list[SignatureMatch] = []
        for orig in original:
            for comp in compare:
                score, details, method = self._field_match_score(orig, comp)
                candidate = SignatureMatch(orig, comp, score, method, details)
                if self._candidate_acceptable(candidate):
                    candidates.append(candidate)

        candidates.sort(
            key=lambda item: (
                item.score or 0.0,
                item.details.get("label_score", 0.0),
                item.details.get("spatial_score", 0.0),
                item.details.get("text_score", 0.0),
            ),
            reverse=True,
        )
        used_original: set[int] = set()
        used_compare: set[int] = set()
        matches: list[SignatureMatch] = []
        for candidate in candidates:
            assert candidate.original is not None and candidate.compare is not None
            orig_id = id(candidate.original)
            comp_id = id(candidate.compare)
            if orig_id in used_original or comp_id in used_compare:
                continue
            used_original.add(orig_id)
            used_compare.add(comp_id)
            matches.append(candidate)

        unmatched_orig = [f for f in original if id(f) not in used_original]
        unmatched_comp = [f for f in compare if id(f) not in used_compare]
        if unmatched_orig and unmatched_comp:
            fallback = self._template_fallback_match(unmatched_orig, unmatched_comp)
            for m in fallback:
                used_original.add(id(m.original))
                used_compare.add(id(m.compare))
            matches.extend(fallback)

        matches.extend(
            SignatureMatch(orig, None, method="signature_unmatched_delete")
            for orig in original
            if id(orig) not in used_original
        )
        matches.extend(
            SignatureMatch(None, comp, method="signature_unmatched_add")
            for comp in compare
            if id(comp) not in used_compare
        )
        return sorted(
            matches,
            key=lambda item: (
                (item.original or item.compare).page_no if (item.original or item.compare) is not None else 0,
                (item.original or item.compare).sort_y if (item.original or item.compare) is not None else 0.0,
                (item.original or item.compare).sort_x if (item.original or item.compare) is not None else 0.0,
            ),
        )

    def _template_fallback_match(
        self,
        unmatched_orig: list[SignatureField],
        unmatched_comp: list[SignatureField],
    ) -> list[SignatureMatch]:
        matches: list[SignatureMatch] = []
        used_comp: set[int] = set()
        for orig in sorted(unmatched_orig, key=lambda f: (f.sort_y, f.sort_x)):
            orig_label = self._base_label(orig)
            best_comp = None
            best_delta = float("inf")
            for comp in unmatched_comp:
                if id(comp) in used_comp:
                    continue
                if comp.column != orig.column:
                    continue
                comp_label = self._base_label(comp)
                if comp_label != orig_label:
                    continue
                delta = abs(comp.sort_y - orig.sort_y)
                if delta < best_delta:
                    best_delta = delta
                    best_comp = comp
            if best_comp is not None and best_delta <= 80.0:
                used_comp.add(id(best_comp))
                matches.append(SignatureMatch(
                    orig, best_comp,
                    score=None,
                    method="signature_template_fallback",
                ))
        return matches

    def _drop_duplicate_unmatched(
        self,
        matches: list[SignatureMatch],
    ) -> list[SignatureMatch]:
        matched_texts = {
            self._normalized(text)
            for match in matches
            if match.original is not None and match.compare is not None
            for text in (match.original.text, match.compare.text)
            if self._normalized(text)
        }
        return [
            match
            for match in matches
            if not (
                (match.original is None) ^ (match.compare is None)
                and self._normalized(
                    (match.original or match.compare).text
                    if (match.original or match.compare) is not None
                    else ""
                )
                in matched_texts
            )
        ]

    def _field_match_score(self, orig: SignatureField, comp: SignatureField) -> tuple[float, dict[str, float], str]:
        left_label = self._base_label(orig)
        right_label = self._base_label(comp)
        label_score = self._label_score(left_label, right_label)
        column_score = 100.0 if orig.column == comp.column else 20.0
        spatial_score = self._spatial_score(orig, comp)
        text_score = self._text_similarity(orig.text, comp.text)
        business_score, business_mismatch = self._business_token_score(orig.text, comp.text)
        w_label, w_column, w_spatial, w_text, w_business = self._match_weights(orig, comp)
        score = (
            label_score * w_label
            + column_score * w_column
            + spatial_score * w_spatial
            + text_score * w_text
            + business_score * w_business
        )
        if left_label in STRONG_VALUE_LABELS and right_label == left_label and business_mismatch:
            score = max(score, 72.0)
        details = {
            "label_score": round(label_score, 2),
            "column_score": round(column_score, 2),
            "spatial_score": round(spatial_score, 2),
            "text_score": round(text_score, 2),
            "business_token_score": round(business_score, 2),
            "business_token_mismatch": 1.0 if business_mismatch else 0.0,
        }
        return round(score, 2), details, self._match_method(left_label, right_label, score, text_score)

    def _match_weights(self, orig: SignatureField, comp: SignatureField) -> tuple[float, float, float, float, float]:
        orig_conf = self._field_confidence(orig)
        comp_conf = self._field_confidence(comp)
        if orig_conf is not None and comp_conf is not None and orig_conf < 0.7 and comp_conf < 0.7:
            return 0.40, 0.22, 0.25, 0.08, 0.05
        return 0.34, 0.18, 0.22, 0.16, 0.10

    def _field_confidence(self, field: SignatureField) -> float | None:
        confidences = [block.confidence for block in field.blocks if block.confidence is not None]
        return sum(confidences) / len(confidences) if confidences else None

    def _candidate_acceptable(self, match: SignatureMatch) -> bool:
        if match.original is None or match.compare is None:
            return False
        score = match.score or 0.0
        left_label = self._base_label(match.original)
        right_label = self._base_label(match.compare)
        if left_label != right_label and UNLABELED_LABEL not in {left_label, right_label}:
            return False
        if match.details.get("column_score", 0.0) < 100.0:
            return False
        if UNLABELED_LABEL in {left_label, right_label}:
            return score >= 58.0 and match.details.get("spatial_score", 0.0) >= 45.0
        return score >= 50.0

    def _label_score(self, left: str, right: str) -> float:
        if left == right:
            return 100.0
        if UNLABELED_LABEL in {left, right}:
            return 58.0
        return 0.0

    def _spatial_score(self, orig: SignatureField, comp: SignatureField) -> float:
        y_distance = abs(orig.sort_y - comp.sort_y)
        x_distance = abs(orig.sort_x - comp.sort_x)
        y_score = max(0.0, 100.0 - y_distance * 2.0)
        x_score = max(0.0, 100.0 - x_distance * 0.4)
        return y_score * 0.75 + x_score * 0.25

    def _text_similarity(self, left: str, right: str) -> float:
        left_norm = self._normalized(left)
        right_norm = self._normalized(right)
        if not left_norm and not right_norm:
            return 100.0
        if not left_norm or not right_norm:
            return 0.0
        if fuzz is not None:
            return float(max(fuzz.ratio(left_norm, right_norm), fuzz.token_set_ratio(left_norm, right_norm)))
        return SequenceMatcher(None, left_norm, right_norm).ratio() * 100.0

    def _business_token_score(self, left: str, right: str) -> tuple[float, bool]:
        left_tokens = self._business_tokens(left)
        right_tokens = self._business_tokens(right)
        if not left_tokens and not right_tokens:
            return 50.0, False
        if not left_tokens or not right_tokens:
            return 25.0, False
        overlap = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens)
        return overlap / max(1, union) * 100.0, overlap == 0

    def _business_tokens(self, text: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", text or "")
        return {
            self._normalized(match.group(0))
            for match in VALUE_TOKEN_PATTERN.finditer(normalized)
            if self._normalized(match.group(0))
        }

    def _match_method(self, left_label: str, right_label: str, score: float, text_score: float) -> str:
        if score >= 99.0 and text_score >= 99.0:
            return "signature_exact"
        if left_label == right_label:
            return "signature_label_position_similarity"
        return "signature_weighted_similarity"

    def _with_occurrence_order(self, fields: list[SignatureField]) -> list[SignatureField]:
        counts: dict[tuple[int, str, str], int] = {}
        ordered: list[SignatureField] = []
        for sig_field in sorted(fields, key=lambda item: (item.page_no, item.sort_y, item.sort_x, item.label)):
            base_key = (sig_field.page_no, sig_field.column, sig_field.label)
            occurrence = counts.get(base_key, 0)
            counts[base_key] = occurrence + 1
            sig_field.label = f"{sig_field.label}#{occurrence}"
            ordered.append(sig_field)
        return ordered

    def _field_key(self, field: SignatureField) -> tuple[int, str, str, int]:
        label, _, occurrence = field.label.partition("#")
        return field.page_no, field.column, label, int(occurrence or "0")

    def _field_base_key(self, field: SignatureField) -> tuple[int, str, str]:
        label, _, _ = field.label.partition("#")
        return field.page_no, field.column, label

    def _field_group_key(self, field: SignatureField) -> tuple[int, str]:
        return field.page_no, field.column

    def _build_diff(
        self,
        match: SignatureMatch,
        index: int,
    ) -> DiffItem | None:
        orig = match.original
        comp = match.compare
        original_text = orig.text if orig else ""
        compare_text = comp.text if comp else ""
        if not self._normalized(original_text) and not self._normalized(compare_text):
            return None
        if self._is_unmatched_empty_label(match):
            return None
        if self._normalized(original_text) == self._normalized(compare_text):
            return None

        if orig is None or not self._normalized(original_text):
            assert comp is not None
            return self._build_add(comp, index, match)
        if comp is None or not self._normalized(compare_text):
            return self._build_delete(orig, index, match)
        return self._build_modify(orig, comp, index, match)

    def _is_unmatched_empty_label(self, match: SignatureMatch) -> bool:
        if match.original is not None and match.compare is not None:
            return False
        field = match.original or match.compare
        return bool(field and field.is_empty_labeled_field)

    def _build_empty_label_structure_diff(
        self,
        field: SignatureField,
        index: int,
        match: SignatureMatch,
    ) -> DiffItem:
        label = self._display_label(field)
        field_text = self._empty_label_text(field)
        is_add = match.original is None
        diff_type = "ADD" if is_add else "DELETE"
        highlight_type = "ADD" if is_add else "DELETE"
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=diff_type,
            title=f"签署栏结构字段：{label}",
            original_text="" if is_add else field_text,
            compare_text=field_text if is_add else "",
            original_snippet="" if is_add else field_text,
            compare_snippet=field_text if is_add else "",
            readable_change=(
                f"新增签署栏结构字段：{label}"
                if is_add
                else f"删除签署栏结构字段：{label}"
            ),
            source_type="signature",
            match_method=match.method,
            match_score=match.score,
            match_score_details=match.details,
            review_flags=["POSSIBLE_PARTIAL_SIGNATURE_REGION"],
            quality_status="NEEDS_REVIEW",
            original_evidence=[] if is_add else self._block_evidence(field, highlight_type),
            compare_evidence=self._block_evidence(field, highlight_type) if is_add else [],
            original_change_ranges=[] if is_add else [TextRange(start=0, end=len(field_text), highlight_type=highlight_type)],
            compare_change_ranges=[TextRange(start=0, end=len(field_text), highlight_type=highlight_type)] if is_add else [],
        )

    def _build_add(self, field: SignatureField, index: int, match: SignatureMatch) -> DiffItem:
        label = self._display_label(field)
        text = field.text
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="ADD",
            title=f"签署栏字段：{label}",
            compare_text=text,
            compare_snippet=text,
            readable_change=f"新增签署栏字段：{label} {text}".strip(),
            source_type="signature",
            match_method=match.method,
            match_score=match.score,
            match_score_details=match.details,
            compare_evidence=self._range_evidence(field, [TextRange(start=0, end=len(text), highlight_type="ADD")], "ADD"),
            compare_change_ranges=[TextRange(start=0, end=len(text), highlight_type="ADD")],
        )

    def _build_delete(self, field: SignatureField, index: int, match: SignatureMatch) -> DiffItem:
        label = self._display_label(field)
        text = field.text
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="DELETE",
            title=f"签署栏字段：{label}",
            original_text=text,
            original_snippet=text,
            readable_change=f"删除签署栏字段：{label} {text}".strip(),
            source_type="signature",
            match_method=match.method,
            match_score=match.score,
            match_score_details=match.details,
            original_evidence=self._range_evidence(field, [TextRange(start=0, end=len(text), highlight_type="DELETE")], "DELETE"),
            original_change_ranges=[TextRange(start=0, end=len(text), highlight_type="DELETE")],
        )

    def _build_modify(self, orig: SignatureField, comp: SignatureField, index: int, match: SignatureMatch) -> DiffItem | None:
        original_snippet, compare_snippet, original_ranges, compare_ranges = changed_snippets(orig.text, comp.text)
        if not original_ranges and not compare_ranges:
            return None
        label = self._display_label(comp)
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="MODIFY",
            title=f"签署栏字段：{label}",
            original_text=orig.text,
            compare_text=comp.text,
            original_snippet=original_snippet,
            compare_snippet=compare_snippet,
            readable_change=f"签署栏字段变更：{label}\n原文：{original_snippet}\n修改后：{compare_snippet}",
            source_type="signature",
            match_method=match.method,
            match_score=match.score,
            match_score_details=match.details,
            original_evidence=self._range_evidence(orig, original_ranges, "MODIFY"),
            compare_evidence=self._range_evidence(comp, compare_ranges, "MODIFY"),
            original_change_ranges=original_ranges,
            compare_change_ranges=compare_ranges,
        )

    def _range_evidence(
        self,
        field: SignatureField,
        ranges: list[TextRange],
        highlight_type: str,
    ) -> list[EvidenceBox]:
        evidences: list[EvidenceBox] = []
        for text_range in ranges:
            if text_range.end <= text_range.start:
                continue
            evidences.extend(self._evidence_for_field_range(field, text_range, highlight_type))
        return evidences or self._block_evidence(field, highlight_type)

    def _evidence_for_field_range(
        self,
        field: SignatureField,
        text_range: TextRange,
        highlight_type: str,
    ) -> list[EvidenceBox]:
        evidences: list[EvidenceBox] = []
        for part, part_start, part_end in self._iter_value_part_ranges(field):
            start = max(text_range.start, part_start)
            end = min(text_range.end, part_end)
            if start >= end:
                continue
            local_start = start - part_start
            local_end = end - part_start
            evidence = self._evidence_for_value_part(part, local_start, local_end, highlight_type)
            if evidence is not None:
                evidences.append(evidence)
        return evidences

    def _iter_value_part_ranges(self, field: SignatureField) -> list[tuple[SignatureValuePart, int, int]]:
        ranges: list[tuple[SignatureValuePart, int, int]] = []
        offset = 0
        for index, part in enumerate(field.value_parts):
            if index > 0:
                offset += 1
            start = offset
            end = start + len(part.text)
            ranges.append((part, start, end))
            offset = end
        return ranges

    def _evidence_for_value_part(
        self,
        part: SignatureValuePart,
        local_start: int,
        local_end: int,
        highlight_type: str,
    ) -> EvidenceBox | None:
        text = part.text[local_start:local_end].strip()
        if not text:
            return None
        char_evidence = self._char_evidence_for_value_part(part, local_start, local_end, text, highlight_type)
        if char_evidence is not None:
            return char_evidence
        bbox = self._estimated_value_bbox(part, local_start, local_end)
        return EvidenceBox(
            page_no=part.block.page_no,
            bbox=bbox,
            method="signature_estimated_text",
            text=text[:300],
            highlight_type=highlight_type,
            confidence=0.74,
            evidence_quality="MEDIUM",
            text_confidence=part.block.confidence,
        )

    def _char_evidence_for_value_part(
        self,
        part: SignatureValuePart,
        local_start: int,
        local_end: int,
        text: str,
        highlight_type: str,
    ) -> EvidenceBox | None:
        block_start = part.block_start
        block_end = part.block_end
        if block_start is None or block_end is None or not part.block.char_boxes:
            return None
        start = block_start + local_start
        end = min(block_end, block_start + local_end)
        if start >= end:
            return None
        chars = self._char_boxes_in_range(part.block, start, end)
        if not chars:
            return None
        return EvidenceBox(
            page_no=chars[0].page_no,
            bbox=self._union_bbox([char.bbox for char in chars]),
            method="signature_char_exact",
            text=text[:300],
            highlight_type=highlight_type,
            confidence=0.98,
            evidence_quality="HIGH",
            text_confidence=self._min_char_confidence(chars) or part.block.confidence,
        )

    def _char_boxes_in_range(self, block: TextBlock, start: int, end: int) -> list[CharBox]:
        indexed = [
            char_box
            for char_box in block.char_boxes
            if char_box.text_index is not None and start <= char_box.text_index < end and char_box.char.strip()
        ]
        if indexed:
            return indexed
        block_text_len = len(block.text or "")
        if len(block.char_boxes) < block_text_len:
            return []
        return [char_box for char_box in block.char_boxes[start:end] if char_box.char.strip()]

    def _estimated_value_bbox(
        self,
        part: SignatureValuePart,
        local_start: int,
        local_end: int,
    ) -> BBox:
        bbox = part.block.bbox
        width = max(1.0, bbox.x1 - bbox.x0)
        source_len = max(1, len(part.block.text or part.text))
        start = local_start
        end = local_end
        if part.block_start is not None:
            start += part.block_start
            end += part.block_start
        x0 = bbox.x0 + width * max(0, min(source_len, start)) / source_len
        x1 = bbox.x0 + width * max(0, min(source_len, end)) / source_len
        if x1 <= x0:
            x1 = min(bbox.x1, x0 + max(2.0, width / source_len))
        return BBox(x0=max(bbox.x0, x0), y0=bbox.y0, x1=min(bbox.x1, x1), y1=bbox.y1)

    def _block_evidence(self, field: SignatureField, highlight_type: str) -> list[EvidenceBox]:
        return [
            EvidenceBox(
                page_no=block.page_no,
                bbox=block.bbox,
                method="signature_field",
                text=(block.text or "")[:300],
                highlight_type=highlight_type,
                confidence=0.74,
                evidence_quality="MEDIUM",
                text_confidence=block.confidence,
            )
            for block in field.blocks
        ]

    def _parse_labeled_text(self, text: str) -> tuple[str | None, str | None]:
        label, value, _, _ = self._parse_labeled_text_with_span(text)
        return label, value

    def _parse_labeled_text_with_span(self, text: str) -> tuple[str | None, str | None, int | None, int | None]:
        compact, index_map = self._compact_with_index_map(text)
        for term in FIELD_TERMS:
            if not compact.startswith(term):
                continue
            label = FIELD_ALIASES.get(term, term)
            start = len(term)
            while start < len(compact) and compact[start] in ":：":
                start += 1
            end = len(compact)
            value = self._clean_value(compact[start:end])
            block_start = index_map[start] if start < len(index_map) else None
            block_end = index_map[end - 1] + 1 if start < end and end - 1 < len(index_map) else None
            return label, value, block_start, block_end
        return None, None, None, None

    def _compact_with_index_map(self, text: str) -> tuple[str, list[int]]:
        normalized = unicodedata.normalize("NFKC", text or "")
        chars: list[str] = []
        index_map: list[int] = []
        for index, char in enumerate(normalized):
            if char.isspace():
                continue
            chars.append(char)
            index_map.append(index)
        return "".join(chars), index_map

    def _clean_value(self, text: str) -> str:
        value = unicodedata.normalize("NFKC", text or "").strip()
        return re.sub(r"\s+", " ", value).strip(" :：")

    def _normalized(self, text: str) -> str:
        value = unicodedata.normalize("NFKC", text or "")
        return PUNCT_PATTERN.sub("", value).lower()

    def _display_label(self, field: SignatureField) -> str:
        label = self._base_label(field)
        return f"{'左栏' if field.column == 'left' else '右栏'}{label}"

    def _base_label(self, field: SignatureField) -> str:
        label, _, _ = field.label.partition("#")
        return label

    def _empty_label_text(self, field: SignatureField) -> str:
        return f"{self._base_label(field)}："

    def _column_split(self, blocks: list[TextBlock], page_width: float) -> float:
        labeled = [block for block in blocks if self._parse_labeled_text(block.text)[0]]
        x_positions = sorted(block.bbox.x0 for block in (labeled or blocks))
        if len(x_positions) >= 2:
            gap, left, right = max(
                ((right - left, left, right) for left, right in zip(x_positions, x_positions[1:], strict=False)),
                key=lambda item: item[0],
            )
            if gap >= 35.0:
                split = (left + right) / 2
                if page_width <= 0 or page_width * 0.15 <= split <= page_width * 0.85:
                    return split
        return page_width / 2 if page_width > 0 else 0.0

    def _column(self, bbox: BBox, split_x: float) -> str:
        return "left" if bbox.x0 < split_x else "right"

    def _field_mid_y(self, field: SignatureField) -> float:
        return sum(self._mid_y(block.bbox) for block in field.blocks) / max(1, len(field.blocks))

    def _mid_y(self, bbox: BBox) -> float:
        return (bbox.y0 + bbox.y1) / 2

    def _union_bbox(self, bboxes: list[BBox]) -> BBox:
        return BBox(
            x0=min(bbox.x0 for bbox in bboxes),
            y0=min(bbox.y0 for bbox in bboxes),
            x1=max(bbox.x1 for bbox in bboxes),
            y1=max(bbox.y1 for bbox in bboxes),
        )

    def _min_char_confidence(self, char_boxes: list[CharBox]) -> float | None:
        confidences = [char_box.confidence for char_box in char_boxes if char_box.confidence is not None]
        return min(confidences) if confidences else None

    def _is_seal_fragment(self, text: str) -> bool:
        compact = self._normalized(text)
        if compact in {self._normalized(item) for item in NOISE_TEXTS}:
            return True
        return "章" in text or "盖章" in text or "合同章" in text or len(compact) <= 1

    def _is_standalone_signature_value(self, text: str, block: TextBlock, split_x: float) -> bool:
        compact = self._normalized(text)
        if not compact:
            return False
        if DATE_PATTERN.fullmatch(compact):
            return True
        if self._column(block.bbox, split_x) == "right" and NAME_PATTERN.fullmatch(text) and text not in NOISE_TEXTS:
            return True
        return len(compact) >= 4 and bool(re.search(r"[0-9A-Za-z\u4e00-\u9fff]", compact))
