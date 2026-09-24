from __future__ import annotations

from app.models import BBox, Clause, ClausePair, Document, Page, TextBlock
from app.services.diff_engine import DiffEngine
from app.services.extractors.contract_v3.table_structure_validator import TableStructureValidator
from app.services.extractors.contract_v3.tables import _publish
from app.services.table_compare import TableComparator
from app.services.table_compare.parser import LogicalTableParser


def _cell(name, row, col, box, rowspan=1, colspan=1):
    return {"cell_id": name, "row": row, "col": col, "rowspan": rowspan,
            "colspan": colspan, "bbox": box}


def _table(cells, rows=2, cols=2):
    return {"rows": rows, "cols": cols, "cells": cells, "table_bbox": [0, 0, 200, 100]}


def _valid():
    return _table([_cell("a", 0, 0, [0, 0, 100, 50]),
                   _cell("b", 0, 1, [100, 0, 200, 50]),
                   _cell("c", 1, 0, [0, 50, 100, 100]),
                   _cell("d", 1, 1, [100, 50, 200, 100])])


def _ocr(*boxes):
    return [{"id": str(i), "text": "text", "bbox": list(box)} for i, box in enumerate(boxes)]


def test_slot_overlap_rejected():
    table = _valid()
    table["cells"][0]["colspan"] = 2
    result = TableStructureValidator().validate(table)
    assert "LOGICAL_SLOT_OVERLAP" in result.reasons


def test_severe_bbox_overlap_rejected():
    table = _valid()
    table["cells"][1]["bbox"] = [40, 0, 140, 50]
    result = TableStructureValidator().validate(table)
    assert "SEVERE_CELL_BBOX_OVERLAP" in result.reasons
    assert result.metrics["severe_bbox_overlaps"][0]["intersection_over_smaller"] >= 0.5


def test_low_ocr_assignment_rejected():
    table = _valid()
    ocr = _ocr([10, 10, 20, 20], [210, 10, 220, 20], [210, 25, 220, 35],
               [210, 40, 220, 50], [210, 55, 220, 65])
    assert "LOW_OCR_ASSIGNMENT" in TableStructureValidator().validate(table, ocr).reasons


def test_uncovered_ocr_row_band_rejected():
    table = _table([_cell("a", 0, 0, [0, 0, 200, 30])], rows=1, cols=1)
    ocr = _ocr([10, 50, 70, 65], [10, 70, 190, 85])
    result = TableStructureValidator().validate(table, ocr)
    assert "UNCOVERED_OCR_ROW_BAND" in result.reasons


def test_valid_table_passes():
    result = TableStructureValidator().validate(_valid(), _ocr([10, 10, 20, 20]))
    assert result.usable and not result.reasons


def test_large_valid_merged_cell_not_rejected_only_by_span():
    result = TableStructureValidator().validate(
        _table([_cell("merged", 0, 0, [0, 0, 200, 100], rowspan=8, colspan=2)], rows=8, cols=2)
    )
    assert result.usable


def _block(html: bool, page=1):
    return TextBlock(block_id="table", page_no=page, block_type="table",
                     bbox=BBox(x0=0, y0=0, x1=200, y1=100),
                     text="A B" if not html else "<table><tr><td>A</td></tr></table>",
                     structure_status="UNSTRUCTURED_TABLE_PRESENT" if not html else "STRUCTURED_TABLE")


def _document(blocks, *, absent=False):
    return Document(filename="test.pdf", path="test.pdf", page_count=1,
                    pages=[Page(page_no=1, width=200, height=100, blocks=blocks,
                                table_presence_status="TABLE_ABSENT" if absent else "UNKNOWN")])


def test_unstructured_present_is_not_delete():
    diffs, _ = TableComparator().build_diffs(_document([_block(True)]), _document([_block(False)]))
    assert len(diffs) == 1 and diffs[0].diff_type == "MODIFY"
    assert diffs[0].match_score_details["decision_reason"] == "TABLE_STRUCTURE_UNRESOLVED"


def test_unstructured_present_is_not_insert():
    diffs, _ = TableComparator().build_diffs(_document([_block(False)]), _document([_block(True)]))
    assert len(diffs) == 1 and diffs[0].diff_type == "MODIFY"
    assert diffs[0].match_score_details["decision_reason"] == "TABLE_STRUCTURE_UNRESOLVED"


def test_true_absent_table_can_delete():
    diffs, _ = TableComparator().build_diffs(_document([_block(True)]), _document([], absent=True))
    assert len(diffs) == 1 and diffs[0].diff_type == "DELETE"


def test_true_absent_table_can_insert():
    diffs, _ = TableComparator().build_diffs(_document([], absent=True), _document([_block(True)]))
    assert len(diffs) == 1 and diffs[0].diff_type == "ADD"


def test_claimed_absence_with_conflicting_table_block_cannot_delete_or_add():
    structured = _document([_block(True)])
    conflicting = _document([_block(False)], absent=True)
    for left, right in ((structured, conflicting), (conflicting, structured)):
        diffs, _ = TableComparator().build_diffs(left, right)
        assert diffs and all(d.diff_type not in {"ADD", "DELETE"} for d in diffs)
        assert any(d.match_score_details.get("decision_reason") == "TABLE_STRUCTURE_UNRESOLVED" for d in diffs)


def test_empty_or_historical_missing_status_is_unknown_not_absent():
    original = _document([_block(True)])
    missing = _document([]).model_dump(exclude={"pages": {0: {"table_presence_status"}}})
    compare = Document.model_validate_json(Document.model_validate(missing).model_dump_json())
    assert compare.pages[0].table_presence_status == "UNKNOWN"
    diffs, _ = TableComparator().build_diffs(original, compare)
    assert diffs and all(d.diff_type not in {"ADD", "DELETE"} for d in diffs)
    assert diffs[0].match_score_details["decision_reason"] == "TABLE_PRESENCE_UNKNOWN"


def test_unparsed_html_and_unaligned_absence_do_not_prove_delete():
    original = _document([_block(True)])
    unparsed = _document([_block(False)])
    unparsed.pages[0].blocks[0].text = "<table><tr>"
    unparsed.pages[0].blocks[0].structure_status = "UNKNOWN"
    diffs, _ = TableComparator().build_diffs(original, unparsed)
    assert diffs and all(d.diff_type != "DELETE" for d in diffs)
    absent_wrong_page = _document([], absent=True)
    absent_wrong_page.pages[0].page_no = 2
    diffs, _ = TableComparator().build_diffs(original, absent_wrong_page)
    assert diffs and all(d.diff_type != "DELETE" for d in diffs)


def test_text_diff_survives_unresolved_table():
    table_diffs, _ = TableComparator().build_diffs(_document([_block(True)]), _document([_block(False)]))
    date_diffs = DiffEngine().build_diffs([ClausePair(
        original=Clause(clause_id="date-original", text="2026 年4 月日", normalized_text="2026年4月日"),
        compare=Clause(clause_id="date-compare", text="2026年4月2日", normalized_text="2026年4月2日"),
    )])
    assert table_diffs[0].diff_type == "MODIFY"
    assert any(d.diff_type == "MODIFY" and "2" in d.compare_text for d in date_diffs)


def test_extraction_state_and_history_compatibility():
    assert LogicalTableParser.structure_state(_block(True)) == "STRUCTURED_TABLE"
    assert LogicalTableParser.structure_state(_block(False)) == "UNSTRUCTURED_TABLE_PRESENT"
    old = _block(True).model_dump(exclude={"structure_status", "table_validation"})
    historical = TextBlock.model_validate_json(TextBlock.model_validate(old).model_dump_json())
    assert historical.structure_status == "UNKNOWN"
    assert LogicalTableParser.structure_state(historical) == "STRUCTURED_TABLE"


def test_extraction_validator_pass_publishes_structured_table():
    artifact = {"blocks": [{"region_id": "r", "text": "A"}]}
    region = {"region_id": "r", "bbox": [0, 0, 200, 100]}
    cell = _cell("a", 0, 0, [0, 0, 200, 100])
    cell["column"] = 0
    table = _publish(artifact, region, [cell],
                     _ocr([10, 10, 20, 20]), "PDF_vector_ruling")
    assert table["structure_status"] == "STRUCTURED_TABLE"
    assert table["validation"]["usable"] and table["html"]
    assert artifact["blocks"][0]["raw_html"]


def test_extraction_validator_fail_keeps_table_evidence_without_cells():
    artifact = {"blocks": [{"region_id": "r", "text": "A B"}]}
    region = {"region_id": "r", "bbox": [0, 0, 200, 100]}
    cells = [_cell("a", 0, 0, [0, 0, 200, 100]), _cell("b", 0, 0, [0, 0, 200, 100])]
    for cell in cells:
        cell["column"] = 0
    table = _publish(artifact, region, cells,
                     _ocr([10, 10, 20, 20]), "raster_cell_graph")
    assert table["structure_status"] == "UNSTRUCTURED_TABLE_PRESENT"
    assert "LOGICAL_SLOT_OVERLAP" in table["validation"]["reasons"]
    assert table["html"] is None and table["cells"] == []
    assert artifact["blocks"][0]["text"] == "A B"
