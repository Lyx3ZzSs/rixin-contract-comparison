from app.models_table import StructuredTable, TableCell, TableRow
from app.services.table_compare.repair_context import BusinessChangeProtector, TableRepairContext
from app.services.table_compare.types import _LogicalRow


def test_table_repair_context_centralizes_source_text_by_block() -> None:
    table = StructuredTable(
        page_no=1,
        rows=[TableRow(row_index=0, cells=[TableCell(row_index=0, col_index=0, text="1")])],
        col_count=1,
        source_block_id="t1",
        source_text="原始 OCR 文本",
    )
    context = TableRepairContext.from_tables([table])
    row = _LogicalRow(
        row_index=0,
        cells=[],
        page_no=1,
        source_block_id="t1",
        source_row=0,
        source_text="行内旧文本",
    )

    assert context.block_id == "t1"
    assert context.source_text_for_row(row) == "原始 OCR 文本"


def test_business_change_protector_marks_high_value_changes() -> None:
    assert BusinessChangeProtector.protected_change_reason("100元", "200元") == "amount_change"
    assert BusinessChangeProtector.protected_change_reason("1套", "2套") == "quantity_change"
    assert BusinessChangeProtector.protected_change_reason("2026年1月1日", "2026年1月2日") == "date_change"
    assert BusinessChangeProtector.protected_change_reason("是", "否") == "business_scalar_change"
    assert BusinessChangeProtector.protected_change_reason("1", "一") == "business_scalar_change"
