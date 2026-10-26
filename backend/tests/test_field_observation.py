from __future__ import annotations

from app.models import BBox
from app.models_table import StructuredTable, TableCell, TableRow
from app.services.diff_quality import DiffQualityProcessor
from app.services.field_observation import ObservationReason, ObservationState
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.diff_builder import SigningRegionDiffBuilder
from app.services.signing_region.models import (
    SigningElement,
    SigningElementType,
    SigningRegion,
    SigningRegionRole,
)
from app.services.table_compare import TableComparator
from app.services.table_compare.diff_builder import TableDiffBuilder
from app.services.table_compare.types import CellDiff, CellDiffGroup


def _region(region_id: str, *elements: SigningElement) -> SigningRegion:
    return SigningRegion(
        region_id=region_id,
        page_no=1,
        bbox=BBox(x0=40, y0=600, x1=560, y1=790),
        region_role=SigningRegionRole.BOTH_PARTIES,
        confidence=0.95,
        confidence_reasons=["two_column_layout"],
        elements=list(elements),
    )


def _field(
    element_id: str,
    text: str,
    *,
    field_key: str = "date",
    state: ObservationState | None = None,
    reason: str = "",
) -> SigningElement:
    return SigningElement(
        element_id=element_id,
        element_type=SigningElementType.FIELD,
        page_no=1,
        bbox=BBox(x0=80, y0=680, x1=260, y1=715),
        text=text,
        confidence=0.95,
        source="inferred",
        observation_state=state,
        observation_reason=reason,
        raw_ref={
            "party_role": "甲方",
            "field_key": field_key,
            "field_label": "签订日期" if field_key == "date" else "单位地址",
            "source_bbox": BBox(x0=80, y0=680, x1=260, y1=715).model_dump(),
            "value_roi": BBox(x0=150, y0=680, x1=260, y1=715).model_dump(),
            **({"value_bbox": BBox(x0=155, y0=683, x1=245, y1=711).model_dump()} if text else {}),
        },
    )


def _change(original: SigningRegion, compare: SigningRegion) -> dict:
    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.95)
    assert len(comparison.field_changes) == 1
    return comparison.field_changes[0]


def test_value_to_seal_occluded_no_ocr_is_unobserved_not_confirmed_delete() -> None:
    original_field = _field(
        "o-address",
        "北京市海淀区",
        field_key="address",
        state=ObservationState.OBSERVED_VALUE,
        reason=ObservationReason.LOCALIZED_PRIMARY_VALUE.value,
    )
    compare_field = _field("c-address", "", field_key="address")
    seal = SigningElement(
        element_id="c-seal",
        element_type=SigningElementType.SEAL,
        page_no=1,
        bbox=BBox(x0=145, y0=675, x1=265, y1=730),
        text="seal",
        confidence=0.95,
        source="visual_model",
    )

    change = _change(_region("O", original_field), _region("C", compare_field, seal))

    assert change["compare_observation_state"] == "UNOBSERVED"
    assert change["compare_observation_reason"] == "SEAL_OCCLUSION"
    assert change["change_decision"] == "NEEDS_REVIEW"
    assert change["fill_state_change"] == ""


def test_value_to_visually_clear_blank_is_confirmed_delete() -> None:
    original = _field(
        "o-date",
        "2026年7月10日",
        state=ObservationState.OBSERVED_VALUE,
        reason=ObservationReason.LOCALIZED_PRIMARY_VALUE.value,
    )
    compare = _field(
        "c-date",
        "",
        state=ObservationState.OBSERVED_BLANK,
        reason=ObservationReason.CLEAR_LOCALIZED_VALUE_ROI.value,
    )

    change = _change(_region("O", original), _region("C", compare))

    assert change["change_decision"] == "CONFIRMED"
    assert change["fill_state_change"] == "cleared"


def test_value_to_business_confirmed_opaque_cover_is_confirmed_delete() -> None:
    original = _field(
        "o-legal-representative",
        "雍正",
        field_key="legal_representative",
        state=ObservationState.OBSERVED_VALUE,
        reason=ObservationReason.LOCALIZED_PRIMARY_VALUE.value,
    )
    compare = _field(
        "c-legal-representative",
        "",
        field_key="legal_representative",
        state=ObservationState.UNOBSERVED,
        reason=ObservationReason.BUSINESS_CONTENT_REMOVED_OR_COVERED.value,
    )

    comparison = SigningRegionComparator().compare(_region("O", original), _region("C", compare))
    change = comparison.field_changes[0]
    diff = SigningRegionDiffBuilder().build_diffs([comparison])[0]
    admitted = DiffQualityProcessor().process([diff]).diffs[0]

    assert change["change_decision"] == "CONFIRMED"
    assert change["fill_state_change"] == "cleared"
    assert admitted.quality_status == "NEEDS_REVIEW"
    assert admitted.match_score_details["change_decision"] == "CONFIRMED"


def test_value_to_missing_roi_remains_unobserved_and_not_confirmed_delete() -> None:
    original = _field(
        "o-legal-representative",
        "雍正",
        field_key="legal_representative",
        state=ObservationState.OBSERVED_VALUE,
        reason=ObservationReason.LOCALIZED_PRIMARY_VALUE.value,
    )
    compare = _field(
        "c-legal-representative",
        "",
        field_key="legal_representative",
        state=ObservationState.UNOBSERVED,
        reason=ObservationReason.FIELD_ROI_NOT_FOUND.value,
    )

    change = _change(_region("O", original), _region("C", compare))

    assert change["change_decision"] == "CANDIDATE"
    assert change["fill_state_change"] == ""


def test_blank_field_to_missing_field_keeps_candidate_instead_of_assuming_equal_blanks() -> None:
    original = _field(
        "o-signature", "", field_key="signature",
        state=ObservationState.OBSERVED_BLANK,
        reason=ObservationReason.CLEAR_LOCALIZED_VALUE_ROI.value,
    )
    original.raw_ref["field_label"] = "签字"
    comparison = SigningRegionComparator().compare(
        _region("O", original, _field("o-date", "2026年7月10日")),
        _region("C", _field("c-date", "2026年7月10日")),
    )
    change = next(item for item in comparison.field_changes if item["field_key"] == "signature")
    assert change["compare_observation_state"] == "UNOBSERVED"
    assert change["change_decision"] == "CANDIDATE"
    assert change["fill_state_change"] == ""
    diff = SigningRegionDiffBuilder().build_diffs([comparison])[0]
    assert diff.quality_status == "NEEDS_REVIEW"


def test_observed_blank_to_handwritten_date_is_confirmed_add() -> None:
    original = _field(
        "o-date",
        "",
        state=ObservationState.OBSERVED_BLANK,
        reason=ObservationReason.CLEAR_LOCALIZED_VALUE_ROI.value,
    )
    compare = _field(
        "c-date",
        "2026年7月10日",
        state=ObservationState.OBSERVED_VALUE,
        reason=ObservationReason.RELIABLE_VISUAL_OBJECT.value,
    )

    change = _change(_region("O", original), _region("C", compare))

    assert change["change_decision"] == "CONFIRMED"
    assert change["fill_state_change"] == "filled"


def test_unobserved_to_value_retains_candidate_without_claiming_blank() -> None:
    original = _field(
        "o-date",
        "",
        state=ObservationState.UNOBSERVED,
        reason=ObservationReason.OCR_EXTRACTION_FAILED.value,
    )
    compare = _field(
        "c-date",
        "2026年7月10日",
        state=ObservationState.OBSERVED_VALUE,
        reason=ObservationReason.LOCALIZED_PRIMARY_VALUE.value,
    )

    comparison = SigningRegionComparator().compare(_region("O", original), _region("C", compare))
    change = comparison.field_changes[0]
    diff = SigningRegionDiffBuilder().build_diffs([comparison])[0]

    assert change["change_decision"] == "NEEDS_REVIEW"
    assert change["fill_state_change"] == ""
    assert "未观察" in diff.readable_change
    assert "未填写变为填写" not in diff.readable_change
    admitted = DiffQualityProcessor().process([diff]).diffs[0]
    assert admitted.quality_status == "NEEDS_REVIEW"
    assert admitted.match_score_details["change_decision"] == "NEEDS_REVIEW"


def test_unobserved_signature_visual_candidate_is_retained_when_seal_occluded() -> None:
    original_field = _field(
        "o-signature-field",
        "",
        field_key="signature",
        state=ObservationState.UNOBSERVED,
        reason=ObservationReason.FIELD_ROI_NOT_FOUND.value,
    )
    compare_field = _field(
        "c-signature-field",
        "",
        field_key="signature",
        state=ObservationState.UNOBSERVED,
        reason=ObservationReason.FIELD_ROI_NOT_FOUND.value,
    )
    signature = SigningElement(
        element_id="c-signature-candidate",
        element_type=SigningElementType.SIGNATURE,
        page_no=1,
        bbox=BBox(x0=150, y0=680, x1=230, y1=720),
        confidence=0.55,
        source="visual_model",
        raw_ref={"party_role": "甲方"},
    )
    seal = SigningElement(
        element_id="c-seal",
        element_type=SigningElementType.SEAL,
        page_no=1,
        bbox=BBox(x0=140, y0=670, x1=245, y1=735),
        confidence=0.92,
        source="visual_model",
        raw_ref={"party_role": "甲方"},
    )

    comparison = SigningRegionComparator().compare(
        _region("original", original_field),
        _region("compare", compare_field, signature, seal),
        match_confidence=0.95,
    )

    assert len(comparison.signature_changes) == 1
    change = comparison.signature_changes[0]
    assert change["change_decision"] == "CANDIDATE"
    assert change["compare_observation_state"] == "UNOBSERVED"
    assert change["compare_observation_reason"] == ObservationReason.VISUAL_OBJECT_CONFLICT.value
    assert change["compare_text"] == "疑似已签（待确认）"


def test_observed_blank_to_missing_generic_field_uses_candidate_mechanism() -> None:
    original_postal = _field(
        "o-postal",
        "",
        field_key="postal_code",
        state=ObservationState.OBSERVED_BLANK,
        reason=ObservationReason.CLEAR_LOCALIZED_VALUE_ROI.value,
    )
    original_address = _field("o-address", "北京", field_key="address")
    compare_address = _field("c-address", "北京", field_key="address")

    comparison = SigningRegionComparator().compare(
        _region("original", original_postal, original_address),
        _region("compare", compare_address),
        match_confidence=0.95,
    )

    change = next(item for item in comparison.template_changes if item["field_key"] == "postal_code")
    assert change["original_observation_state"] == "OBSERVED_BLANK"
    assert change["compare_observation_state"] == "UNOBSERVED"
    assert change["reason"] == "EMPTY_FIELD_TEMPLATE_CHANGE"


def test_both_unobserved_generic_values_require_review() -> None:
    original = _field(
        "o-address",
        "北京市海淀区",
        field_key="address",
        state=ObservationState.UNOBSERVED,
        reason=ObservationReason.CROSS_COLUMN_EVIDENCE_REJECTED.value,
    )
    compare = _field(
        "c-address",
        "北京市路27号",
        field_key="address",
        state=ObservationState.UNOBSERVED,
        reason=ObservationReason.CROSS_COLUMN_EVIDENCE_REJECTED.value,
    )

    change = _change(_region("original", original), _region("compare", compare))

    assert change["decision_reason"] == "BOTH_SIDES_UNOBSERVED"
    assert change["change_decision"] == "NEEDS_REVIEW"


def test_observed_blank_to_missing_critical_field_requires_review() -> None:
    original_credit_code = _field(
        "o-credit-code",
        "",
        field_key="credit_code",
        state=ObservationState.OBSERVED_BLANK,
        reason=ObservationReason.CLEAR_LOCALIZED_VALUE_ROI.value,
    )
    original_address = _field("o-address", "北京", field_key="address")
    compare_address = _field("c-address", "北京", field_key="address")

    comparison = SigningRegionComparator().compare(
        _region("original", original_credit_code, original_address),
        _region("compare", compare_address),
        match_confidence=0.95,
    )

    change = next(item for item in comparison.template_changes if item["field_key"] == "credit_code")
    assert change["original_observation_state"] == "OBSERVED_BLANK"
    assert change["compare_observation_state"] == "UNOBSERVED"
    assert change["reason"] == "EMPTY_FIELD_TEMPLATE_CHANGE"


def test_two_empty_localized_clear_rois_are_observed_blank_without_diff() -> None:
    original = _field("o-date", "")
    compare = _field("c-date", "")

    comparison = SigningRegionComparator().compare(_region("O", original), _region("C", compare))

    assert comparison.field_changes == []
    assert original.observation_state == ObservationState.OBSERVED_BLANK
    assert compare.observation_state == ObservationState.OBSERVED_BLANK


def test_rejected_cross_column_evidence_is_unobserved_not_blank() -> None:
    original = _field("o-address", "", field_key="address")
    original.raw_ref["rejected_contaminated_evidence"] = [{"rejection_reason": "CROSS_COLUMN_SOURCE_BLOCK"}]
    compare = _field(
        "c-address",
        "北京市海淀区",
        field_key="address",
        state=ObservationState.OBSERVED_VALUE,
        reason=ObservationReason.LOCALIZED_PRIMARY_VALUE.value,
    )

    change = _change(_region("O", original), _region("C", compare))

    assert change["original_observation_state"] == "UNOBSERVED"
    assert change["original_observation_reason"] == "CROSS_COLUMN_EVIDENCE_REJECTED"
    assert change["change_decision"] == "NEEDS_REVIEW"


def test_missing_party_element_retains_column_candidate() -> None:
    original = _field(
        "o-party",
        "南京公司",
        field_key="party_name",
        state=ObservationState.OBSERVED_VALUE,
        reason=ObservationReason.LOCALIZED_PRIMARY_VALUE.value,
    )
    original.raw_ref["party_role"] = "乙方"
    compare = _field(
        "c-date",
        "",
        state=ObservationState.OBSERVED_BLANK,
        reason=ObservationReason.CLEAR_LOCALIZED_VALUE_ROI.value,
    )

    comparison = SigningRegionComparator().compare(_region("O", original), _region("C", compare))

    column = next(item for item in comparison.column_changes if item["party_role"] == "乙方")
    assert column["change_decision"] == "CANDIDATE"
    assert column["compare_observation_state"] == "UNOBSERVED"
    assert "OBSERVATION_UNOBSERVED" in comparison.review_flags


def test_missing_column_ocr_retains_column_candidate() -> None:
    original = _field(
        "o-address",
        "北京市海淀区",
        field_key="address",
        state=ObservationState.OBSERVED_VALUE,
        reason=ObservationReason.LOCALIZED_PRIMARY_VALUE.value,
    )

    comparison = SigningRegionComparator().compare(_region("O", original), _region("C"))
    change = comparison.column_changes[0]

    assert change["type"] == "DELETE"
    assert change["compare_observation_state"] == "UNOBSERVED"
    assert change["change_decision"] == "CANDIDATE"


def test_table_missing_cell_is_unobserved_but_clear_empty_cell_is_blank() -> None:
    clear_blank = StructuredTable(
        page_no=1,
        col_count=1,
        rows=[
            TableRow(
                row_index=0,
                cells=[TableCell(row_index=0, col_index=0, text="", bbox=BBox(x0=10, y0=10, x1=80, y1=30))],
            )
        ],
        geometry_status="ok",
        geometry_confidence=0.9,
    )
    value = StructuredTable(
        page_no=1,
        col_count=1,
        rows=[
            TableRow(
                row_index=0,
                cells=[TableCell(row_index=0, col_index=0, text="100", bbox=BBox(x0=10, y0=10, x1=80, y1=30))],
            )
        ],
        geometry_status="ok",
        geometry_confidence=0.9,
    )
    observed = TableDiffBuilder._group_observation_details(
        CellDiffGroup(
            0,
            [
                CellDiff(
                    0,
                    0,
                    "",
                    "100",
                    "ADD",
                    original_row=0,
                    compare_row=0,
                    original_col=0,
                    compare_col=0,
                )
            ],
        ),
        clear_blank,
        value,
    )
    missing = TableDiffBuilder._group_observation_details(
        CellDiffGroup(
            0,
            [
                CellDiff(
                    0,
                    0,
                    "100",
                    "",
                    "DELETE",
                    original_row=0,
                    compare_row=None,
                    original_col=0,
                    compare_col=None,
                )
            ],
        ),
        value,
        clear_blank,
    )

    assert observed["original_observation_state"] == "OBSERVED_BLANK"
    assert observed["change_decision"] == "CONFIRMED"
    assert missing["compare_observation_state"] == "UNOBSERVED"
    assert missing["change_decision"] == "NEEDS_REVIEW"


def test_aligned_empty_table_cell_retains_roi_coordinates_for_blank_observation() -> None:
    def table(values: list[str]) -> StructuredTable:
        return StructuredTable(
            page_no=1,
            col_count=2,
            rows=[
                TableRow(
                    row_index=0,
                    cells=[
                        TableCell(
                            row_index=0,
                            col_index=column,
                            text=text,
                            bbox=BBox(x0=column * 100, y0=10, x1=(column + 1) * 100, y1=40),
                        )
                        for column, text in enumerate(values)
                    ],
                )
            ],
            geometry_status="ok",
            geometry_confidence=0.9,
        )

    builder = TableComparator()._diff_builder
    original = table(["付款节点", ""])
    compare = table(["付款节点", "4528.30"])
    cell_diffs = builder.diff_cells(original, compare)

    assert len(cell_diffs) == 1
    cell_diff = cell_diffs[0]
    assert (cell_diff.original_row, cell_diff.original_col) == (0, 1)
    details = builder._group_observation_details(CellDiffGroup(0, cell_diffs), original, compare)
    assert details["original_observation_state"] == "OBSERVED_BLANK"
    assert details["change_decision"] == "CONFIRMED"


def test_localized_empty_cell_can_be_blank_despite_global_table_geometry_conflict() -> None:
    table = StructuredTable(
        page_no=1,
        col_count=2,
        rows=[
            TableRow(
                row_index=0,
                cells=[
                    TableCell(
                        row_index=0,
                        col_index=column,
                        text="",
                        bbox=BBox(x0=column * 100, y0=10, x1=(column + 1) * 100, y1=40),
                    )
                    for column in range(2)
                ],
            )
        ],
        geometry_status="severe_conflict",
        geometry_confidence=0.2,
        bbox_cell_count=2,
        html_cell_count=2,
    )

    observation = TableDiffBuilder._cell_observation(table, 0, 1, "")

    assert observation.state == ObservationState.OBSERVED_BLANK
    assert observation.reason == ObservationReason.CLEAR_LOCALIZED_CELL_ROI.value


def test_rejected_table_column_value_is_unobserved_not_blank() -> None:
    table = StructuredTable(
        page_no=1,
        col_count=1,
        rows=[
            TableRow(
                row_index=0,
                cells=[
                    TableCell(
                        row_index=0,
                        col_index=0,
                        text="串列污染值",
                        bbox=BBox(x0=10, y0=10, x1=100, y1=40),
                    )
                ],
            )
        ],
        geometry_status="ok",
        geometry_confidence=0.9,
        bbox_cell_count=1,
        html_cell_count=1,
    )

    observation = TableDiffBuilder._cell_observation(table, 0, 0, "")

    assert observation.state == ObservationState.UNOBSERVED
    assert observation.reason == ObservationReason.CROSS_COLUMN_EVIDENCE_REJECTED.value


def test_missing_table_cell_can_be_blank_when_row_and_column_grid_localize_roi() -> None:
    rows = []
    for row in range(3):
        cells = []
        for column in range(3):
            if row == 0 and column == 2:
                continue
            cells.append(
                TableCell(
                    row_index=row,
                    col_index=column,
                    text="",
                    bbox=BBox(
                        x0=column * 100,
                        y0=row * 30,
                        x1=(column + 1) * 100,
                        y1=(row + 1) * 30,
                    ),
                )
            )
        rows.append(TableRow(row_index=row, cells=cells))
    table = StructuredTable(
        page_no=1,
        col_count=3,
        rows=rows,
        geometry_status="severe_conflict",
        geometry_confidence=0.2,
    )

    observation = TableDiffBuilder._cell_observation(table, 0, 2, "")

    assert observation.state == ObservationState.OBSERVED_BLANK
    assert observation.reason == ObservationReason.INFERRED_CLEAR_CELL_ROI.value
