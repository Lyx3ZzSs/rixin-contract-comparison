from __future__ import annotations

from app.models import DiffItem, EvidenceBox, TextRange
from app.services.signature_compare import normalizer, patterns
from app.services.signature_compare.types import SignatureField, SignatureMatch
from app.utils.id_utils import generate_diff_id


class SignatureDiffBuilder:
    def build_diffs(self, matches: list[SignatureMatch], start_index: int) -> list[DiffItem]:
        diffs: list[DiffItem] = []
        next_index = start_index
        for match in matches:
            orig = match.original
            comp = match.compare
            if orig and comp and normalizer.value_equal(orig.field_value, comp.field_value):
                continue
            if orig and comp:
                diff = self._make_modify(orig, comp, match, next_index)
            elif orig:
                diff = self._make_delete(orig, next_index)
            elif comp:
                diff = self._make_add(comp, next_index)
            else:
                continue
            diffs.append(diff)
            next_index += 1
        return diffs

    def _make_modify(self, orig: SignatureField, comp: SignatureField, match: SignatureMatch, index: int) -> DiffItem:
        title = self._title(orig)
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="MODIFY",
            title=title,
            original_text=orig.field_value,
            compare_text=comp.field_value,
            original_snippet=orig.field_value,
            compare_snippet=comp.field_value,
            readable_change=f"{title}变更：{orig.field_value} → {comp.field_value}",
            source_type="signature",
            section_type="signature",
            match_score=match.score,
            match_method=match.match_method,
            match_confidence="NORMAL" if match.score >= 0.82 else "LOW",
            review_flags=self._review_flags(orig, comp),
            quality_status="NEEDS_REVIEW",
            text_confidence=min(orig.confidence, comp.confidence),
            original_evidence=[self._evidence(orig, "MODIFY")],
            compare_evidence=[self._evidence(comp, "MODIFY")],
            original_change_ranges=[TextRange(start=0, end=len(orig.field_value), highlight_type="MODIFY")],
            compare_change_ranges=[TextRange(start=0, end=len(comp.field_value), highlight_type="MODIFY")],
        )

    def _make_delete(self, field: SignatureField, index: int) -> DiffItem:
        title = self._title(field)
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="DELETE",
            title=title,
            original_text=field.field_value,
            original_snippet=field.field_value,
            readable_change=f"{title}删除：{field.field_value}",
            source_type="signature",
            section_type="signature",
            match_method="signature_field_unmatched",
            match_confidence="LOW",
            review_flags=self._review_flags(field, None),
            quality_status="NEEDS_REVIEW",
            text_confidence=field.confidence,
            original_evidence=[self._evidence(field, "DELETE")],
            original_change_ranges=[TextRange(start=0, end=len(field.field_value), highlight_type="DELETE")],
        )

    def _make_add(self, field: SignatureField, index: int) -> DiffItem:
        title = self._title(field)
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="ADD",
            title=title,
            compare_text=field.field_value,
            compare_snippet=field.field_value,
            readable_change=f"{title}新增：{field.field_value}",
            source_type="signature",
            section_type="signature",
            match_method="signature_field_unmatched",
            match_confidence="LOW",
            review_flags=self._review_flags(None, field),
            quality_status="NEEDS_REVIEW",
            text_confidence=field.confidence,
            compare_evidence=[self._evidence(field, "ADD")],
            compare_change_ranges=[TextRange(start=0, end=len(field.field_value), highlight_type="ADD")],
        )

    @staticmethod
    def _title(field: SignatureField) -> str:
        party = field.party_label or patterns.PARTY_LABELS.get(field.party_role, "签字页")
        label = patterns.FIELD_KEY_LABELS.get(field.field_key, field.field_label)
        return f"签字页：{party}-{label}"

    @staticmethod
    def _review_flags(original: SignatureField | None, compare: SignatureField | None) -> list[str]:
        fields = [field for field in (original, compare) if field is not None]
        flags = ["SIGNATURE_SECTION_REVIEW"]
        if any(field.confidence < 0.72 for field in fields):
            flags.append("LOW_CONFIDENCE_SIGNATURE_EVIDENCE")
        for field in fields:
            flags.extend(field.review_flags)
        return list(dict.fromkeys(flags))

    @staticmethod
    def _evidence(field: SignatureField, highlight_type: str) -> EvidenceBox:
        return EvidenceBox(
            page_no=field.page_no,
            bbox=field.bbox,
            method=field.source_method,
            text=field.field_value[:300],
            highlight_type=highlight_type,
            confidence=field.confidence,
            evidence_quality="HIGH" if field.confidence >= 0.8 else "MEDIUM",
        )
