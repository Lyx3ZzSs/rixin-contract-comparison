from __future__ import annotations

from app.models import BBox, DiffItem, EvidenceBox, TextRange
from app.services.signing_region.models import (
    SigningElement,
    SigningElementType,
    SigningRegion,
    SigningRegionComparison,
)
from app.utils.id_utils import generate_diff_id


READABLE_CHANGE_FIELDS = (
    ("region_changes", "签署栏变化"),
    ("column_changes", "签署栏变化"),
    ("party_changes", "签署主体变化"),
    ("field_changes", "签署字段变化"),
    ("seal_changes", "印章文字变化"),
    ("date_changes", "签署日期变化"),
    ("signature_changes", "签字文字变化"),
    ("label_changes", "签章标签变化"),
    ("table_changes", "签章表格变化"),
    ("visual_changes", "签章视觉区域变化"),
)


class SigningRegionDiffBuilder:
    def build_diffs(self, comparisons: list[SigningRegionComparison], start_index: int = 1) -> list[DiffItem]:
        diffs: list[DiffItem] = []
        next_index = start_index
        for comparison in comparisons:
            if comparison.diff_type is None:
                continue
            if comparison.region_changes or comparison.original_region is None or comparison.compare_region is None:
                diffs.append(self._to_diff(comparison, next_index))
                next_index += 1
                continue
            for changes_attr, _ in READABLE_CHANGE_FIELDS:
                if changes_attr == "region_changes":
                    continue
                for change in getattr(comparison, changes_attr):
                    diffs.append(self._to_change_diff(comparison, changes_attr, change, next_index))
                    next_index += 1
        return diffs

    def _to_change_diff(
        self,
        comparison: SigningRegionComparison,
        changes_attr: str,
        change: dict,
        index: int,
    ) -> DiffItem:
        original_text = str(change.get("original_text") or "")
        compare_text = str(change.get("compare_text") or "")
        change_type = str(change.get("type") or "MODIFY")
        role = str(change.get("party_role") or "")
        label = str(change.get("field_label") or self._change_label(changes_attr))
        prefix = f"{role} · " if role and role != "unknown" else ""
        title = f"{prefix}{label}"
        original_evidence = self._change_evidence(comparison.original_region, change, side="original")
        compare_evidence = self._change_evidence(comparison.compare_region, change, side="compare")
        flags = self._change_flags(changes_attr, comparison.review_flags)
        original_display = self._change_display_text(change, side="original")
        compare_display = self._change_display_text(change, side="compare")
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=change_type,
            title=title,
            original_text=original_text,
            compare_text=compare_text,
            original_snippet=original_text,
            compare_snippet=compare_text,
            readable_change=f"{title}：{original_display} → {compare_display}",
            source_type="signing_region",
            section_type=f"signature:{self._change_kind(changes_attr)}",
            match_method="signing_region",
            match_confidence=comparison.match_confidence,
            review_flags=flags,
            original_evidence=original_evidence,
            compare_evidence=compare_evidence,
            original_change_ranges=[TextRange(start=0, end=len(original_text), highlight_type=change_type)]
            if original_text
            else [],
            compare_change_ranges=[TextRange(start=0, end=len(compare_text), highlight_type=change_type)]
            if compare_text
            else [],
        )

    @staticmethod
    def _change_display_text(change: dict, *, side: str) -> str:
        text = str(change.get(f"{side}_text") or change.get(f"{side}_residual_text") or "")
        residual = str(change.get(f"{side}_residual_text") or "")
        if residual:
            return f"主体缺失（仅残留格式文本“{residual}”）"
        return text or "空白"

    @staticmethod
    def _change_kind(changes_attr: str) -> str:
        return {
            "party_changes": "party",
            "column_changes": "column",
            "field_changes": "field",
            "seal_changes": "seal",
            "date_changes": "date",
            "signature_changes": "signature",
            "label_changes": "label",
            "table_changes": "table",
            "visual_changes": "visual",
        }.get(changes_attr, "field")

    @staticmethod
    def _change_label(changes_attr: str) -> str:
        return dict(READABLE_CHANGE_FIELDS).get(changes_attr, "签署字段")

    @staticmethod
    def _change_flags(changes_attr: str, flags: list[str]) -> list[str]:
        relevant = {
            "party_changes": {"SIGNING_PARTY_CHANGE", "CRITICAL_VALUE_CHANGE"},
            "column_changes": {"SIGNING_COLUMN_CHANGE"},
            "field_changes": {"SIGNING_FIELD_CHANGE", "CRITICAL_VALUE_CHANGE"},
            "seal_changes": {"SIGNING_SEAL_CHANGE"},
            "date_changes": {"SIGNING_DATE_CHANGE", "CRITICAL_VALUE_CHANGE"},
            "signature_changes": {"SIGNING_SIGNATURE_CHANGE", "SIGNING_SIGNATURE_UNCERTAIN"},
            "label_changes": {"SIGNING_LABEL_CHANGE"},
            "table_changes": {"SIGNING_TABLE_CHANGE"},
            "visual_changes": {"SIGNING_VISUAL_CHANGE"},
        }.get(changes_attr, set())
        relevant.add("SIGNING_MATCH_LOW_CONFIDENCE")
        return [flag for flag in dict.fromkeys(flags) if flag in relevant]

    @staticmethod
    def _change_evidence(
        region: SigningRegion | None,
        change: dict,
        *,
        side: str,
    ) -> list[EvidenceBox]:
        if region is None:
            return []
        change_type = str(change.get("type") or "MODIFY")
        if (change_type == "ADD" and side == "original") or (change_type == "DELETE" and side == "compare"):
            return []
        element_id = str(change.get(f"{side}_element_id") or "")
        role = str(change.get("party_role") or "")
        field_key = str(change.get("field_key") or "")
        element_type = str(change.get("element_type") or "")
        candidates = [element for element in region.elements if not element_id or element.element_id == element_id]
        if not element_id:
            candidates = [
                element
                for element in candidates
                if (not element_type or element.element_type.value == element_type)
                and (not role or SigningRegionDiffBuilder._element_role(region, element) == role)
                and (
                    not field_key
                    or element.element_type == SigningElementType.SIGNATURE
                    or str(element.raw_ref.get("field_key") or "") == field_key
                )
            ]
        element = candidates[0] if candidates else None
        if element is None:
            return [SigningRegionDiffBuilder._evidence(region, change.get("type") or "MODIFY")]
        text = str(change.get(f"{side}_text") or change.get(f"{side}_residual_text") or "")
        if change.get("change_scope") == "value" and not text:
            return []
        bboxes = [element.bbox]
        if change.get("change_scope") == "value":
            if element.raw_ref.get("value_bboxes"):
                bboxes = [BBox.model_validate(item) for item in element.raw_ref["value_bboxes"]]
            elif element.raw_ref.get("value_bbox"):
                bboxes = [BBox.model_validate(element.raw_ref["value_bbox"])]
        elif change.get("change_scope") == "field" and element.raw_ref.get("field_bboxes"):
            bboxes = [BBox.model_validate(item) for item in element.raw_ref["field_bboxes"]]
        evidence_texts = [text] * len(bboxes)
        if change.get("change_scope") == "field" and len(element.raw_ref.get("field_segments") or []) == len(bboxes):
            evidence_texts = [str(item) for item in element.raw_ref["field_segments"]]
        return [
            EvidenceBox(
                page_no=element.page_no,
                bbox=bbox,
                method="signing_region_element",
                text=evidence_text,
                highlight_type=change.get("type") or "MODIFY",
                confidence=element.confidence,
                evidence_quality="HIGH" if element.confidence >= 0.75 else "MEDIUM",
            )
            for bbox, evidence_text in zip(bboxes, evidence_texts, strict=True)
        ]

    @staticmethod
    def _element_role(region: SigningRegion, element: SigningElement) -> str:
        explicit = str(element.raw_ref.get("party_role") or "")
        if explicit:
            return explicit
        if region.region_role.value == "party_a":
            return "甲方"
        if region.region_role.value == "party_b":
            return "乙方"
        if region.region_role.value == "both_parties":
            midpoint = (region.bbox.x0 + region.bbox.x1) / 2
            return "甲方" if (element.bbox.x0 + element.bbox.x1) / 2 < midpoint else "乙方"
        return "unknown"

    def _to_diff(self, comparison: SigningRegionComparison, index: int) -> DiffItem:
        original_text = self._summary(comparison.original_region)
        compare_text = self._summary(comparison.compare_region)
        changed_text = self._readable_change(comparison)
        original_party_snippet = self._party_snippet(comparison, side="original")
        compare_party_snippet = self._party_snippet(comparison, side="compare")
        original_evidence = self._party_evidence(comparison, side="original")
        compare_evidence = self._party_evidence(comparison, side="compare")
        has_region_level_changes = self._has_region_level_changes(comparison)
        region_highlight_type = (
            comparison.diff_type if has_region_level_changes or not comparison.party_changes else None
        )
        region_evidence_method = (
            "signing_region_visual" if comparison.party_changes and has_region_level_changes else "signing_region"
        )
        if comparison.original_region is not None:
            original_evidence.append(
                self._evidence(
                    comparison.original_region,
                    region_highlight_type,
                    method=region_evidence_method,
                )
            )
        if comparison.compare_region is not None:
            compare_evidence.append(
                self._evidence(
                    comparison.compare_region,
                    region_highlight_type,
                    method=region_evidence_method,
                )
            )
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=comparison.diff_type or "MODIFY",
            title=self._title(comparison),
            original_text=original_text,
            compare_text=compare_text,
            original_snippet=original_party_snippet or original_text,
            compare_snippet=compare_party_snippet or compare_text,
            readable_change=changed_text,
            source_type="signing_region",
            section_type="signature",
            review_flags=list(dict.fromkeys(comparison.review_flags)),
            original_evidence=original_evidence,
            compare_evidence=compare_evidence,
            original_change_ranges=[
                TextRange(start=0, end=len(original_text), highlight_type=comparison.diff_type or "MODIFY")
            ]
            if original_text
            else [],
            compare_change_ranges=[
                TextRange(start=0, end=len(compare_text), highlight_type=comparison.diff_type or "MODIFY")
            ]
            if compare_text
            else [],
        )

    @staticmethod
    def _summary(region: SigningRegion | None) -> str:
        if region is None:
            return ""
        parts = [
            element.text.strip()
            for element in region.elements
            if element.element_type != SigningElementType.PARTY_FIELD and element.text.strip()
        ]
        return "；".join(parts)

    @staticmethod
    def _party_snippet(comparison: SigningRegionComparison, *, side: str) -> str:
        field_name = f"{side}_text"
        return "\n".join(
            str(change.get(field_name) or "").strip()
            for change in comparison.party_changes
            if str(change.get(field_name) or "").strip()
        )

    @staticmethod
    def _party_evidence(
        comparison: SigningRegionComparison,
        *,
        side: str,
    ) -> list[EvidenceBox]:
        region = comparison.original_region if side == "original" else comparison.compare_region
        if region is None:
            return []
        elements_by_role = {
            str(element.raw_ref.get("party_role") or ""): element
            for element in region.elements
            if element.element_type == SigningElementType.PARTY_FIELD
        }
        evidences: list[EvidenceBox] = []
        for change in comparison.party_changes:
            role = str(change.get("party_role") or "")
            element = elements_by_role.get(role)
            text = str(change.get(f"{side}_text") or "").strip()
            if element is None or not text:
                continue
            change_type = str(change.get("type") or "MODIFY")
            if change_type == "ADD" and side == "original":
                continue
            if change_type == "DELETE" and side == "compare":
                continue
            evidences.append(
                EvidenceBox(
                    page_no=element.page_no,
                    bbox=element.bbox,
                    method="signing_region",
                    text=text,
                    highlight_type=change_type,
                    confidence=element.confidence,
                    evidence_quality="HIGH" if element.confidence >= 0.75 else "MEDIUM",
                )
            )
        return evidences

    @staticmethod
    def _has_region_level_changes(comparison: SigningRegionComparison) -> bool:
        return bool(
            comparison.region_changes
            or comparison.column_changes
            or comparison.seal_changes
            or comparison.date_changes
            or comparison.signature_changes
            or comparison.label_changes
            or comparison.table_changes
            or comparison.visual_changes
        )

    @staticmethod
    def _title(comparison: SigningRegionComparison) -> str:
        original = comparison.original_region
        compare = comparison.compare_region
        if original is not None and compare is not None and original.page_no != compare.page_no:
            return f"签署区（原第{original.page_no}页 / 新第{compare.page_no}页）"
        region = original or compare
        page_no = region.page_no if region is not None else 0
        return f"签署区（第{page_no}页）"

    @staticmethod
    def _readable_change(comparison: SigningRegionComparison) -> str:
        messages: list[str] = []
        for field_name, label in READABLE_CHANGE_FIELDS:
            for change in getattr(comparison, field_name):
                if change.get("detail"):
                    messages.append(str(change["detail"]))
                else:
                    messages.append(f"{label}：{change.get('original_text', '')} → {change.get('compare_text', '')}")
        return "；".join(messages) or "签章区发生变化"

    @staticmethod
    def _evidence(
        region: SigningRegion | None,
        highlight_type: str | None,
        *,
        method: str = "signing_region",
    ) -> EvidenceBox:
        assert region is not None
        return EvidenceBox(
            page_no=region.page_no,
            bbox=region.bbox,
            method=method,
            text=SigningRegionDiffBuilder._summary(region)[:300],
            highlight_type=highlight_type,
            confidence=region.confidence,
            evidence_quality="HIGH" if region.confidence >= 0.75 else "MEDIUM",
        )
