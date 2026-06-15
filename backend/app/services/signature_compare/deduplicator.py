from __future__ import annotations

from dataclasses import replace

from app.models import BBox, DiffItem, EvidenceBox
from app.services.signature_compare import normalizer
from app.services.signature_compare.types import SignatureField, SignatureFieldCandidate


class SignatureDeduplicator:
    def deduplicate_fields(self, fields: list[SignatureField]) -> list[SignatureField]:
        best: dict[tuple[str, str], SignatureField] = {}
        conflicts: set[tuple[str, str]] = set()
        for field in fields:
            if not normalizer.is_actual_value(field.field_value):
                continue
            key = self._winner_key(field)
            current = best.get(key)
            if current is not None and not normalizer.value_equal(current.field_value, field.field_value):
                conflicts.add(key)
            if current is None or self._should_replace_field(current, field):
                best[key] = field
        fields = [
            self._with_review_flag(field, "SIGNATURE_FIELD_SOURCE_CONFLICT")
            if key in conflicts else field
            for key, field in best.items()
        ]
        return sorted(fields, key=lambda item: (item.page_no, item.bbox.y0, item.bbox.x0, item.party_role, item.field_key))

    def duplicate_rejection_candidates(
        self,
        fields: list[SignatureField],
        kept_fields: list[SignatureField],
    ) -> list[SignatureFieldCandidate]:
        kept_ids = {id(field) for field in kept_fields}
        rejected: list[SignatureFieldCandidate] = []
        for field in fields:
            if id(field) in kept_ids:
                continue
            kept = self._matching_kept_field(field, kept_fields)
            rejected.append(SignatureFieldCandidate(
                side=field.side,
                page_no=field.page_no,
                party_role=field.party_role,
                party_label=field.party_label,
                field_key=field.field_key,
                field_label=field.field_label,
                raw_value=field.field_value,
                normalized_value=normalizer.normalize_value(field.field_value),
                bbox=field.bbox,
                confidence=field.confidence,
                source_method=field.source_method,
                source_block_ids=field.source_block_ids,
                status="rejected",
                reject_reason=(
                    "duplicate_lower_priority"
                    if kept is not None and normalizer.value_equal(field.field_value, kept.field_value)
                    else "field_conflict_lower_priority"
                ),
            ))
        return rejected

    def remove_suppressed_non_body_diffs(
        self,
        table_diffs: list[DiffItem],
        seal_diffs: list[DiffItem],
        signature_diffs: list[DiffItem],
    ) -> tuple[list[DiffItem], list[DiffItem]]:
        suppressed_table = set(self.suppressed_non_body_diff_ids(table_diffs, signature_diffs, source_type="table"))
        suppressed_seal = set(self.suppressed_non_body_diff_ids(seal_diffs, signature_diffs, source_type="seal"))
        return (
            [diff for diff in table_diffs if diff.diff_id not in suppressed_table],
            [diff for diff in seal_diffs if diff.diff_id not in suppressed_seal],
        )

    def remove_suppressed_table_diffs(
        self,
        table_diffs: list[DiffItem],
        signature_diffs: list[DiffItem],
    ) -> list[DiffItem]:
        return self.remove_suppressed_non_body_diffs(table_diffs, [], signature_diffs)[0]

    def suppressed_table_diff_ids(
        self,
        table_diffs: list[DiffItem],
        signature_diffs: list[DiffItem],
    ) -> list[str]:
        return self.suppressed_non_body_diff_ids(table_diffs, signature_diffs, source_type="table")

    def suppressed_seal_diff_ids(
        self,
        seal_diffs: list[DiffItem],
        signature_diffs: list[DiffItem],
    ) -> list[str]:
        return self.suppressed_non_body_diff_ids(seal_diffs, signature_diffs, source_type="seal")

    def suppressed_non_body_diff_ids(
        self,
        non_body_diffs: list[DiffItem],
        signature_diffs: list[DiffItem],
        *,
        source_type: str,
    ) -> list[str]:
        if not non_body_diffs or not signature_diffs:
            return []
        result: list[str] = []
        for diff in non_body_diffs:
            if diff.source_type != source_type:
                continue
            if source_type == "table" and not self._looks_like_signature_table_diff(diff):
                continue
            if source_type == "seal" and not self._looks_like_signature_seal_diff(diff):
                continue
            if any(self._non_body_diff_covered_by_signature(diff, signature_diff) for signature_diff in signature_diffs):
                result.append(diff.diff_id)
        return result

    @staticmethod
    def _field_priority(field: SignatureField) -> tuple[float, int]:
        method_rank = {
            "signature_seal": 4,
            "signature_table_cell": 3,
            "signature_text": 2,
            "signature_field": 1,
        }.get(field.source_method, 0)
        value_quality = SignatureDeduplicator._field_value_quality(field)
        risk_penalty = sum(
            1
            for flag in field.review_flags
            if flag in {
                "SIGNATURE_FIELD_SOURCE_CONFLICT",
                "SIGNATURE_COLUMN_ROLE_CONFLICT",
                "SIGNATURE_ROLE_INFERRED_BY_POSITION",
            }
        )
        return method_rank - risk_penalty, field.confidence, value_quality, len(field.field_value)

    def _should_replace_field(self, current: SignatureField, candidate: SignatureField) -> bool:
        if normalizer.value_equal(current.field_value, candidate.field_value):
            return self._field_priority(candidate) > self._field_priority(current)
        if self._field_priority(candidate) > self._field_priority(current):
            return True
        return False

    @staticmethod
    def _field_value_quality(field: SignatureField) -> int:
        value = field.field_value
        score = 0
        if any(token in value for token in ("公司", "有限", "集团", "银行", "支行")):
            score += 3
        if any(char.isdigit() for char in value):
            score += 1
        if len(normalizer.normalize_value(value)) >= 4:
            score += 1
        if any(token in value for token in ("开户", "户银", "帐", "账", "税", "传")) and field.field_key in {"phone", "fax"}:
            score -= 4
        return score

    @staticmethod
    def _with_review_flag(field: SignatureField, flag: str) -> SignatureField:
        if flag in field.review_flags:
            return field
        return replace(field, review_flags=[*field.review_flags, flag])

    @staticmethod
    def _matching_kept_field(field: SignatureField, kept_fields: list[SignatureField]) -> SignatureField | None:
        for kept in kept_fields:
            if SignatureDeduplicator._winner_key(kept) == SignatureDeduplicator._winner_key(field):
                return kept
        return None

    @staticmethod
    def _winner_key(field: SignatureField) -> tuple[str, str]:
        if field.field_key == "seal_text":
            return f"page:{field.page_no}", field.field_key
        zone = field.column_zone if field.column_zone in {"left", "right"} else "unknown"
        return f"{field.party_role}:{zone}", field.field_key

    @staticmethod
    def _looks_like_signature_table_diff(diff: DiffItem) -> bool:
        text = f"{diff.title} {diff.original_text} {diff.compare_text}"
        return normalizer.looks_like_signature_text(text) or "表格字段：联系人" in diff.title

    @staticmethod
    def _looks_like_signature_seal_diff(diff: DiffItem) -> bool:
        return diff.source_type == "seal"

    def _non_body_diff_covered_by_signature(self, table_diff: DiffItem, signature_diff: DiffItem) -> bool:
        sig_values = [signature_diff.original_text, signature_diff.compare_text]
        table_text = normalizer.normalize_value(f"{table_diff.original_text} {table_diff.compare_text}")
        if any(value and normalizer.normalize_value(value) in table_text for value in sig_values):
            return True
        sig_evidences = [*signature_diff.original_evidence, *signature_diff.compare_evidence]
        table_evidences = [*table_diff.original_evidence, *table_diff.compare_evidence]
        return any(self._evidence_overlap(left, right) >= 0.65 for left in table_evidences for right in sig_evidences)

    def _evidence_overlap(self, left: EvidenceBox, right: EvidenceBox) -> float:
        if left.page_no != right.page_no:
            return 0.0
        intersection = self._intersection_area(left.bbox, right.bbox)
        smaller = min(self._area(left.bbox), self._area(right.bbox))
        if smaller <= 0:
            return 0.0
        return intersection / smaller

    @staticmethod
    def _intersection_area(left: BBox, right: BBox) -> float:
        width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
        height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
        return width * height

    @staticmethod
    def _area(bbox: BBox) -> float:
        return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)
