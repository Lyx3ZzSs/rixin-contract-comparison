from __future__ import annotations

import re
import unicodedata

from app.models import BBox, Document
from app.models_table import StructuredTable
from app.services.signature_compare import normalizer, patterns
from app.services.signature_compare.types import (
    PendingSignatureLabel,
    SignatureExtractionResult,
    SignatureField,
    SignatureFieldCandidate,
)
from app.services.table_compare import utils as table_utils
from app.services.table_compare.parser import LogicalTableParser


class SignatureFieldExtractor:
    def __init__(self) -> None:
        self._table_parser = LogicalTableParser()

    def extract(self, document: Document, side: str) -> SignatureExtractionResult:
        result = SignatureExtractionResult()
        signature_pages = self._signature_pages(document)
        result.fields.extend(self._extract_table_fields(document, side, signature_pages, result.candidates))
        result.fields.extend(self._extract_text_fields(document, side, signature_pages, result.candidates))
        result.fields.extend(self._extract_seal_fields(document, side, signature_pages, result.candidates))
        return result

    def extract_fields(self, document: Document, side: str) -> list[SignatureField]:
        return self.extract(document, side).fields

    def _signature_pages(self, document: Document) -> set[int]:
        pages: set[int] = set()
        for page in document.pages:
            page_text = " ".join(block.text or "" for block in page.blocks)
            if page.semantic_role == "signature" or patterns.SIGNATURE_TERMS.search(page_text):
                pages.add(page.page_no)
                continue
            if any((block.block_role == "signature" or block.semantic_role == "signature") for block in page.blocks):
                pages.add(page.page_no)
        return pages

    def _extract_table_fields(
        self,
        document: Document,
        side: str,
        signature_pages: set[int],
        candidates: list[SignatureFieldCandidate],
    ) -> list[SignatureField]:
        table_blocks = [
            item
            for item in self._table_parser.table_blocks(document)
            if item[0].page_no in signature_pages or patterns.SIGNATURE_TERMS.search(item[1] or "")
        ]
        fields: list[SignatureField] = []
        for table in self._table_parser.parse_tables(table_blocks):
            if self._looks_like_signature_table(table):
                fields.extend(self._fields_from_table(table, side, candidates))
        return fields

    def _extract_text_fields(
        self,
        document: Document,
        side: str,
        signature_pages: set[int],
        candidates: list[SignatureFieldCandidate],
    ) -> list[SignatureField]:
        fields: list[SignatureField] = []
        for page in document.pages:
            if page.page_no not in signature_pages:
                continue
            for block in page.blocks:
                if (block.block_type or "").lower() in {"table", "table_cell", "seal", "stamp"}:
                    continue
                if not normalizer.looks_like_signature_text(block.text or ""):
                    if block.block_role == "signature" or block.semantic_role == "signature":
                        candidates.append(self._candidate(
                            side=side,
                            page_no=block.page_no,
                            party_role="unknown",
                            party_label=patterns.PARTY_LABELS["unknown"],
                            field_key="unknown",
                            field_label="",
                            raw_value=block.text or "",
                            bbox=block.bbox,
                            confidence=0.3,
                            source_method="signature_text",
                            source_block_ids=[block.block_id],
                            status="rejected",
                            reject_reason="no_signature_field_pattern",
                        ))
                    continue
                party_role, party_label = normalizer.role_from_text(block.text) or ("unknown", patterns.PARTY_LABELS["unknown"])
                labeled = normalizer.labeled_values(block.text or "")
                if not labeled:
                    stamp_field = self._stamp_field_from_text(block.text or "")
                    if stamp_field is not None:
                        stamp_role, stamp_label, stamp_value = stamp_field
                        fields.append(self._field(
                            side=side,
                            page_no=block.page_no,
                            party_role=stamp_role,
                            party_label=stamp_label,
                            field_key="seal_text",
                            field_label=patterns.FIELD_KEY_LABELS["seal_text"],
                            field_value=stamp_value,
                            bbox=block.bbox,
                            confidence=0.74,
                            source_method="signature_text",
                            source_block_ids=[block.block_id],
                        ))
                        candidates.append(self._accepted_candidate(
                            side,
                            block.page_no,
                            stamp_role,
                            stamp_label,
                            "seal_text",
                            patterns.FIELD_KEY_LABELS["seal_text"],
                            stamp_value,
                            block.bbox,
                            0.74,
                            "signature_text",
                            [block.block_id],
                        ))
                    else:
                        candidates.append(self._candidate(
                            side=side,
                            page_no=block.page_no,
                            party_role=party_role,
                            party_label=party_label,
                            field_key="unknown",
                            field_label="",
                            raw_value=block.text or "",
                            bbox=block.bbox,
                            confidence=0.45,
                            source_method="signature_text",
                            source_block_ids=[block.block_id],
                            status="rejected",
                            reject_reason="no_labeled_value_or_stamp",
                        ))
                    continue
                for label, value, key in labeled:
                    if normalizer.is_actual_value(value):
                        fields.append(self._field(
                            side=side,
                            page_no=block.page_no,
                            party_role=party_role,
                            party_label=party_label,
                            field_key=key,
                            field_label=label,
                            field_value=value,
                            bbox=block.bbox,
                            confidence=0.68,
                            source_method="signature_text",
                            source_block_ids=[block.block_id],
                        ))
                        candidates.append(self._accepted_candidate(
                            side,
                            block.page_no,
                            party_role,
                            party_label,
                            key,
                            label,
                            value,
                            block.bbox,
                            0.68,
                            "signature_text",
                            [block.block_id],
                        ))
                    else:
                        candidates.append(self._candidate(
                            side=side,
                            page_no=block.page_no,
                            party_role=party_role,
                            party_label=party_label,
                            field_key=key,
                            field_label=label,
                            raw_value=value,
                            bbox=block.bbox,
                            confidence=0.4,
                            source_method="signature_text",
                            source_block_ids=[block.block_id],
                            status="rejected",
                            reject_reason="template_or_empty_value",
                        ))
        return fields

    def _stamp_field_from_text(self, text: str) -> tuple[str, str, str] | None:
        match = patterns.PARTY_STAMP_PATTERN.search(unicodedata.normalize("NFKC", text or ""))
        if not match:
            return None
        role = normalizer.role_from_text(match.group(1))
        if role is None:
            return None
        value = normalizer.clean_value(match.group(2))
        if not normalizer.is_actual_value(value):
            return None
        return role[0], role[1], value

    def _extract_seal_fields(
        self,
        document: Document,
        side: str,
        signature_pages: set[int],
        candidates: list[SignatureFieldCandidate],
    ) -> list[SignatureField]:
        fields: list[SignatureField] = []
        for page in document.pages:
            if page.page_no not in signature_pages:
                continue
            for block in page.blocks:
                if (block.block_type or "").lower() not in {"seal", "stamp"}:
                    continue
                value = normalizer.clean_value(block.text or "")
                if not normalizer.is_actual_value(value):
                    candidates.append(self._candidate(
                        side=side,
                        page_no=block.page_no,
                        party_role="unknown",
                        party_label=patterns.PARTY_LABELS["unknown"],
                        field_key="seal_text",
                        field_label=patterns.FIELD_KEY_LABELS["seal_text"],
                        raw_value=value,
                        bbox=block.layout_bbox or block.bbox,
                        confidence=0.35,
                        source_method="signature_seal",
                        source_block_ids=[block.block_id],
                        status="rejected",
                        reject_reason="template_or_empty_value",
                    ))
                    continue
                party_role, party_label = normalizer.role_from_x(block.bbox, page.width)
                fields.append(self._field(
                    side=side,
                    page_no=block.page_no,
                    party_role=party_role,
                    party_label=party_label,
                    field_key="seal_text",
                    field_label=patterns.FIELD_KEY_LABELS["seal_text"],
                    field_value=value,
                    bbox=block.layout_bbox or block.bbox,
                    confidence=0.85,
                    source_method="signature_seal",
                    source_block_ids=[block.block_id],
                ))
                candidates.append(self._accepted_candidate(
                    side,
                    block.page_no,
                    party_role,
                    party_label,
                    "seal_text",
                    patterns.FIELD_KEY_LABELS["seal_text"],
                    value,
                    block.layout_bbox or block.bbox,
                    0.85,
                    "signature_seal",
                    [block.block_id],
                ))
        return fields

    def _fields_from_table(
        self,
        table: StructuredTable,
        side: str,
        candidates: list[SignatureFieldCandidate],
    ) -> list[SignatureField]:
        role_by_col = self._roles_by_column(table)
        previous_by_col: dict[int, SignatureField] = {}
        pending_by_col: dict[int, PendingSignatureLabel] = {}
        fields: list[SignatureField] = []

        for row in table.rows:
            for cell in sorted(row.cells, key=lambda item: item.col_index):
                text = cell.text or ""
                text_compact = normalizer.compact(text)
                if not text_compact:
                    continue
                column_zone = self._column_zone(cell.bbox, table)
                role = role_by_col.get(cell.col_index)
                if role is None:
                    role = normalizer.role_from_x(cell.bbox, self._table_width(table)) if cell.bbox else ("unknown", patterns.PARTY_LABELS["unknown"])
                party_role, party_label = role
                role_review_flags = self._role_review_flags(party_role, role_by_col, cell.col_index, column_zone)
                if normalizer.role_from_text(text) and len(text_compact) <= 12:
                    candidates.append(self._candidate(
                        side=side,
                        page_no=table.page_no,
                        party_role=party_role,
                        party_label=party_label,
                        field_key="role_header",
                        field_label="角色",
                        raw_value=text,
                        bbox=cell.bbox,
                        confidence=0.5,
                        source_method="signature_table_cell",
                        source_block_ids=[table.source_block_id] if table.source_block_id else [],
                        status="rejected",
                        reject_reason="role_header_only",
                        column_zone=column_zone,
                    ))
                    continue

                labeled = normalizer.labeled_values(text)
                if labeled:
                    for label, value, key in labeled:
                        if not normalizer.is_actual_value(value) or not self._value_allowed_for_key(value, key):
                            candidates.append(self._candidate(
                                side=side,
                                page_no=table.page_no,
                                party_role=party_role,
                                party_label=party_label,
                                field_key=key,
                                field_label=label,
                                raw_value=value,
                                bbox=cell.bbox,
                                confidence=0.42,
                                source_method="signature_table_cell",
                                source_block_ids=[table.source_block_id] if table.source_block_id else [],
                                status="rejected",
                                reject_reason=(
                                    "label_fragment_value"
                                    if normalizer.is_actual_value(value)
                                    else "pending_label_without_value"
                                ),
                                column_zone=column_zone,
                            ))
                            pending_by_col[cell.col_index] = PendingSignatureLabel(
                                party_role=party_role,
                                party_label=party_label,
                                field_key=key,
                                field_label=label,
                                bbox=cell.bbox,
                                source_block_ids=[table.source_block_id] if table.source_block_id else [],
                                column_zone=column_zone,
                            )
                            previous_by_col.pop(cell.col_index, None)
                            continue
                        field = self._field(
                            side=side,
                            page_no=table.page_no,
                            party_role=party_role,
                            party_label=party_label,
                            field_key=key,
                            field_label=label,
                            field_value=value,
                            bbox=cell.bbox,
                            confidence=0.82,
                            source_method="signature_table_cell",
                            source_block_ids=[table.source_block_id] if table.source_block_id else [],
                            review_flags=role_review_flags,
                            column_zone=column_zone,
                        )
                        fields.append(field)
                        candidates.append(self._accepted_candidate(
                            side,
                            table.page_no,
                            party_role,
                            party_label,
                            key,
                            label,
                            value,
                            cell.bbox,
                            0.82,
                            "signature_table_cell",
                            [table.source_block_id] if table.source_block_id else [],
                            column_zone=column_zone,
                        ))
                        previous_by_col[cell.col_index] = field
                        pending_by_col.pop(cell.col_index, None)
                    continue

                pending_item = self._pending_label_for_cell(pending_by_col, role_by_col, cell.col_index, column_zone)
                if pending_item and normalizer.is_actual_value(text):
                    pending_col, pending = pending_item
                    field = self._field(
                        side=side,
                        page_no=table.page_no,
                        party_role=pending.party_role,
                        party_label=pending.party_label,
                        field_key=pending.field_key,
                        field_label=pending.field_label,
                        field_value=text,
                        bbox=normalizer.union_bbox(pending.bbox, cell.bbox) if pending.bbox else cell.bbox,
                        confidence=0.74,
                        source_method="signature_table_cell",
                        source_block_ids=pending.source_block_ids,
                        review_flags=[*role_review_flags, "SIGNATURE_CROSS_CELL_VALUE"],
                        column_zone=pending.column_zone,
                    )
                    fields.append(field)
                    candidates.append(self._accepted_candidate(
                        side,
                        table.page_no,
                        pending.party_role,
                        pending.party_label,
                        pending.field_key,
                        pending.field_label,
                        text,
                        cell.bbox,
                        0.74,
                        "signature_table_cell",
                        pending.source_block_ids,
                        column_zone=pending.column_zone,
                    ))
                    previous_by_col[cell.col_index] = field
                    pending_by_col.pop(pending_col, None)
                    continue

                previous = previous_by_col.get(cell.col_index)
                previous = previous or self._nearby_previous_continuation(previous_by_col, cell.col_index, column_zone)
                if previous and previous.field_key in patterns.CONTINUATION_FIELDS and normalizer.is_continuation_value(text, previous.field_key):
                    field = self._field(
                        side=side,
                        page_no=table.page_no,
                        party_role=previous.party_role,
                        party_label=previous.party_label,
                        field_key=previous.field_key,
                        field_label=previous.field_label,
                        field_value=f"{previous.field_value}{normalizer.clean_value(text)}",
                        bbox=normalizer.union_bbox(previous.bbox, cell.bbox),
                        confidence=min(previous.confidence, 0.76),
                        source_method=previous.source_method,
                        source_block_ids=previous.source_block_ids,
                        review_flags=[*previous.review_flags, "SIGNATURE_CONTINUED_FIELD"],
                        column_zone=previous.column_zone,
                    )
                    fields = [item for item in fields if item is not previous]
                    fields.append(field)
                    candidates.append(self._accepted_candidate(
                        side,
                        table.page_no,
                        previous.party_role,
                        previous.party_label,
                        previous.field_key,
                        previous.field_label,
                        field.field_value,
                        field.bbox,
                        min(previous.confidence, 0.76),
                        previous.source_method,
                        previous.source_block_ids,
                        column_zone=previous.column_zone,
                    ))
                    previous_by_col[cell.col_index] = field
        return fields

    def _accepted_candidate(
        self,
        side: str,
        page_no: int,
        party_role: str,
        party_label: str,
        field_key: str,
        field_label: str,
        raw_value: str,
        bbox: BBox | None,
        confidence: float,
        source_method: str,
        source_block_ids: list[str],
        column_zone: str = "unknown",
    ) -> SignatureFieldCandidate:
        return self._candidate(
            side=side,
            page_no=page_no,
            party_role=party_role,
            party_label=party_label,
            field_key=field_key,
            field_label=field_label,
            raw_value=raw_value,
            bbox=bbox,
            confidence=confidence,
            source_method=source_method,
            source_block_ids=source_block_ids,
            status="accepted",
            reject_reason="",
            column_zone=column_zone,
        )

    @staticmethod
    def _candidate(
        *,
        side: str,
        page_no: int,
        party_role: str,
        party_label: str,
        field_key: str,
        field_label: str,
        raw_value: str,
        bbox: BBox | None,
        confidence: float,
        source_method: str,
        source_block_ids: list[str],
        status: str,
        reject_reason: str,
        column_zone: str = "unknown",
    ) -> SignatureFieldCandidate:
        return SignatureFieldCandidate(
            side=side,
            page_no=page_no,
            party_role=party_role,
            party_label=party_label,
            field_key=field_key,
            field_label=field_label,
            raw_value=raw_value,
            normalized_value=normalizer.normalize_value(raw_value),
            bbox=bbox,
            confidence=confidence,
            source_method=source_method,
            source_block_ids=source_block_ids,
            status=status,
            reject_reason=reject_reason,
            column_zone=column_zone,
        )

    def _pending_label_for_cell(
        self,
        pending_by_col: dict[int, PendingSignatureLabel],
        role_by_col: dict[int, tuple[str, str]],
        col_index: int,
        column_zone: str,
    ) -> tuple[int, PendingSignatureLabel] | None:
        if col_index in pending_by_col:
            pending = pending_by_col[col_index]
            if pending.column_zone == column_zone or pending.column_zone == "unknown" or column_zone == "unknown":
                return col_index, pending
            return None
        if col_index - 1 in pending_by_col:
            pending = pending_by_col[col_index - 1]
            if pending.column_zone not in {column_zone, "unknown"} and column_zone != "unknown":
                return None
            target_role = role_by_col.get(col_index)
            if (
                target_role is not None
                and target_role[0] != pending.party_role
                and not target_role[0].startswith("unknown")
            ):
                return None
            return col_index - 1, pending
        return None

    @staticmethod
    def _nearby_previous_continuation(
        previous_by_col: dict[int, SignatureField],
        col_index: int,
        column_zone: str,
    ) -> SignatureField | None:
        for candidate_col in (col_index, col_index - 1, col_index + 1):
            previous = previous_by_col.get(candidate_col)
            if (
                previous
                and previous.field_key in patterns.CONTINUATION_FIELDS
                and (previous.column_zone == column_zone or previous.column_zone == "unknown" or column_zone == "unknown")
            ):
                return previous
        return None

    @staticmethod
    def _role_review_flags(
        party_role: str,
        role_by_col: dict[int, tuple[str, str]],
        col_index: int,
        column_zone: str,
    ) -> list[str]:
        flags: list[str] = []
        if party_role.startswith("unknown"):
            flags.append("SIGNATURE_ROLE_INFERRED_BY_POSITION")
        if col_index not in role_by_col:
            flags.append("SIGNATURE_ROLE_INFERRED_BY_POSITION")
        if (
            (party_role in {"supplier", "party_a", "seller"} and column_zone == "right")
            or (party_role in {"buyer", "party_b", "purchaser"} and column_zone == "left")
        ):
            flags.append("SIGNATURE_COLUMN_ROLE_CONFLICT")
        return list(dict.fromkeys(flags))

    def _roles_by_column(self, table: StructuredTable) -> dict[int, tuple[str, str]]:
        result: dict[int, tuple[str, str]] = {}
        for row in table.rows[:3]:
            for cell in row.cells:
                role = normalizer.role_from_text(cell.text)
                if role:
                    for col in range(cell.col_index, cell.col_index + max(cell.colspan, 1)):
                        result[col] = role
        if result:
            return result
        if table.col_count >= 2:
            result[0] = ("unknown_left", patterns.PARTY_LABELS["unknown_left"])
            result[table.col_count - 1] = ("unknown_right", patterns.PARTY_LABELS["unknown_right"])
        return result

    @staticmethod
    def _field(
        *,
        side: str,
        page_no: int,
        party_role: str,
        party_label: str,
        field_key: str,
        field_label: str,
        field_value: str,
        bbox: BBox | None,
        confidence: float,
        source_method: str,
        source_block_ids: list[str],
        review_flags: list[str] | None = None,
        column_zone: str = "unknown",
    ) -> SignatureField:
        return SignatureField(
            side=side,
            page_no=page_no,
            party_role=party_role,
            party_label=party_label,
            field_key=field_key,
            field_label=field_label,
            field_value=normalizer.clean_value(field_value),
            bbox=bbox or BBox(x0=0, y0=0, x1=0, y1=0),
            confidence=confidence,
            source_method=source_method,
            source_block_ids=source_block_ids,
            review_flags=review_flags or [],
            column_zone=column_zone,
        )

    @staticmethod
    def _table_width(table: StructuredTable) -> float:
        bboxes = [cell.bbox for row in table.rows for cell in row.cells if cell.bbox]
        if not bboxes:
            return 0.0
        return max(bbox.x1 for bbox in bboxes)

    @staticmethod
    def _table_x_bounds(table: StructuredTable) -> tuple[float, float] | None:
        bboxes = [cell.bbox for row in table.rows for cell in row.cells if cell.bbox]
        if not bboxes:
            return None
        return min(bbox.x0 for bbox in bboxes), max(bbox.x1 for bbox in bboxes)

    def _column_zone(self, bbox: BBox | None, table: StructuredTable) -> str:
        bounds = self._table_x_bounds(table)
        if bbox is None or bounds is None:
            return "unknown"
        left, right = bounds
        if right <= left:
            return "unknown"
        center = (bbox.x0 + bbox.x1) / 2
        return "left" if center <= (left + right) / 2 else "right"

    @staticmethod
    def _looks_like_signature_table(table: StructuredTable) -> bool:
        return normalizer.looks_like_signature_text(table.all_cell_text()) or table_utils.looks_like_contact_signature_text(table.all_cell_text())

    @staticmethod
    def _value_allowed_for_key(value: str, field_key: str) -> bool:
        compact = normalizer.compact(normalizer.clean_value(value))
        if not compact:
            return False
        if field_key in {"phone", "fax"}:
            return bool(re.search(r"\d{5,}", compact)) and not bool(
                re.search(r"(开户|户银|银行|账号|帐号|税号|邮政|政编)", compact)
            )
        if field_key in {"account", "tax_no", "postcode"}:
            return bool(re.search(r"[A-Za-z0-9]{4,}", compact)) and not bool(
                re.search(r"(开户|银行|传真|电话)", compact)
            )
        if field_key == "bank":
            return bool("银行" in compact or "支行" in compact or "分行" in compact)
        return True
