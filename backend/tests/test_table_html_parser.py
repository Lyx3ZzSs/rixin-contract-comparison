from app.models import BBox
from app.services.table_compare.html_parser import parse_html_tables


class TestSimpleTable:
    def test_basic_2x3_table(self):
        html = "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr><tr><td>E</td><td>F</td></tr></table>"
        tables = parse_html_tables(html)
        assert len(tables) == 1
        t = tables[0]
        assert len(t.rows) == 3
        assert t.col_count == 2
        assert t.rows[0].cells[0].text == "A"
        assert t.rows[2].cells[1].text == "F"

    def test_adjacent_same_values_not_merged(self):
        html = "<table><tr><td>序号</td><td>单价</td><td>金额</td></tr><tr><td>1</td><td>8500</td><td>8500</td></tr></table>"
        t = parse_html_tables(html)[0]
        assert t.col_count == 3
        row1 = t.rows[1]
        assert len(row1.cells) == 3
        assert row1.cells[1].text == "8500"
        assert row1.cells[1].colspan == 1
        assert row1.cells[2].text == "8500"
        assert row1.cells[2].colspan == 1

    def test_empty_cells_preserved(self):
        html = "<table><tr><td></td><td>B</td><td></td></tr></table>"
        t = parse_html_tables(html)[0]
        assert t.rows[0].cells[0].text == ""
        assert t.rows[0].cells[1].text == "B"
        assert t.rows[0].cells[2].text == ""

    def test_th_treated_as_td(self):
        html = "<table><tr><th>Header</th></tr><tr><td>Value</td></tr></table>"
        t = parse_html_tables(html)[0]
        assert t.rows[0].cells[0].text == "Header"
        assert t.rows[1].cells[0].text == "Value"

    def test_cell_text_whitespace_stripped(self):
        html = "<table><tr><td>  hello  world  </td></tr></table>"
        t = parse_html_tables(html)[0]
        assert t.rows[0].cells[0].text == "hello  world"

    def test_no_table_returns_empty(self):
        html = "<div><p>No table here</p></div>"
        tables = parse_html_tables(html)
        assert tables == []


class TestColspan:
    def test_colspan_9(self):
        html = '<table><tr><td colspan="9">Full width</td></tr><tr><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td>7</td><td>8</td><td>9</td></tr></table>'
        t = parse_html_tables(html)[0]
        assert t.col_count == 9
        assert len(t.rows[0].cells) == 1
        assert t.rows[0].cells[0].colspan == 9
        assert t.rows[0].cells[0].text == "Full width"
        assert len(t.rows[1].cells) == 9

    def test_colspan_partial(self):
        html = '<table><tr><td colspan="2">AB</td><td>C</td></tr></table>'
        t = parse_html_tables(html)[0]
        assert t.col_count == 3
        assert len(t.rows[0].cells) == 2
        assert t.rows[0].cells[0].colspan == 2
        assert t.rows[0].cells[1].text == "C"

    def test_colspan_with_summary_row(self):
        html = '<table><tr><td>A</td><td>B</td><td>C</td></tr><tr><td colspan="2">小计</td><td>100</td></tr></table>'
        t = parse_html_tables(html)[0]
        assert t.col_count == 3
        summary = t.rows[1]
        assert summary.cells[0].colspan == 2
        assert summary.cells[0].text == "小计"
        assert summary.cells[1].text == "100"


class TestRowspan:
    def test_basic_rowspan(self):
        html = '<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table>'
        t = parse_html_tables(html)[0]
        assert len(t.rows) == 2
        assert t.rows[0].cells[0].rowspan == 2
        assert t.rows[0].cells[0].text == "A"
        assert len(t.rows[1].cells) == 1
        assert t.rows[1].cells[0].text == "C"

    def test_colspan_and_rowspan_combined(self):
        html = '<table><tr><td colspan="2" rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr><tr><td>D</td><td>E</td><td>F</td></tr></table>'
        t = parse_html_tables(html)[0]
        assert len(t.rows) == 3
        assert t.col_count == 3
        assert t.rows[0].cells[0].colspan == 2
        assert t.rows[0].cells[0].rowspan == 2


class TestPPStructureFormat:
    def test_div_html_body_wrapper(self):
        html = '<div style="text-align: center;"><html><body><table border="1"><tr><td>X</td><td>Y</td></tr></table></body></html></div>'
        t = parse_html_tables(html)[0]
        assert len(t.rows) == 1
        assert t.col_count == 2

    def test_with_tbody(self):
        html = '<table border="1"><tbody><tr><td>A</td><td>B</td></tr></tbody></table>'
        t = parse_html_tables(html)[0]
        assert t.rows[0].cells[0].text == "A"

    def test_multiple_tables_in_html(self):
        html = '<table><tr><td>T1</td></tr></table><table><tr><td>T2</td></tr></table>'
        tables = parse_html_tables(html)
        assert len(tables) == 2
        assert tables[0].rows[0].cells[0].text == "T1"
        assert tables[1].rows[0].cells[0].text == "T2"


class TestStructuredTableHelpers:
    def test_get_cell(self):
        html = '<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>'
        t = parse_html_tables(html)[0]
        assert t.get_cell(0, 0).text == "A"
        assert t.get_cell(1, 1).text == "D"
        assert t.get_cell(5, 5) is None

    def test_all_cell_text(self):
        html = '<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>'
        t = parse_html_tables(html)[0]
        assert t.all_cell_text() == "A B C D"

    def test_source_block_id_preserved(self):
        html = "<table><tr><td>X</td></tr></table>"
        t = parse_html_tables(html, source_block_id="p3_b5")[0]
        assert t.source_block_id == "p3_b5"


class TestCellBboxes:
    def test_cell_bboxes_assigned(self):
        html = "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
        bboxes = [
            BBox(x0=0, y0=0, x1=50, y1=20),
            BBox(x0=50, y0=0, x1=100, y1=20),
            BBox(x0=0, y0=20, x1=50, y1=40),
            BBox(x0=50, y0=20, x1=100, y1=40),
        ]
        t = parse_html_tables(html, cell_bboxes=bboxes)[0]
        assert t.get_cell(0, 0).bbox == BBox(x0=0, y0=0, x1=50, y1=20)
        assert t.get_cell(0, 1).bbox == BBox(x0=50, y0=0, x1=100, y1=20)
        assert t.get_cell(1, 0).bbox == BBox(x0=0, y0=20, x1=50, y1=40)
        assert t.get_cell(1, 1).bbox == BBox(x0=50, y0=20, x1=100, y1=40)

    def test_cell_bboxes_none_when_not_provided(self):
        html = "<table><tr><td>X</td></tr></table>"
        t = parse_html_tables(html)[0]
        assert t.get_cell(0, 0).bbox is None

    def test_cell_bboxes_with_colspan(self):
        html = '<table><tr><td colspan="2">AB</td><td>C</td></tr></table>'
        bboxes = [
            BBox(x0=0, y0=0, x1=100, y1=20),
            BBox(x0=100, y0=0, x1=150, y1=20),
        ]
        t = parse_html_tables(html, cell_bboxes=bboxes)[0]
        assert t.get_cell(0, 0).bbox == BBox(x0=0, y0=0, x1=100, y1=20)
        assert t.get_cell(0, 2).bbox == BBox(x0=100, y0=0, x1=150, y1=20)

    def test_bbox_grid_corrects_minor_column_conflict(self):
        html = "<table><tr><td>A</td><td>B</td></tr></table>"
        bboxes = [
            BBox(x0=50, y0=0, x1=90, y1=20),
            BBox(x0=0, y0=0, x1=40, y1=20),
        ]

        t = parse_html_tables(html, cell_bboxes=bboxes)[0]

        assert t.geometry_status == "minor_conflict"
        assert t.get_cell(0, 0).text == "B"
        assert t.get_cell(0, 1).text == "A"

    def test_bbox_count_mismatch_marks_low_confidence(self):
        html = "<table><tr><td>A</td><td>B</td></tr></table>"
        t = parse_html_tables(
            html,
            cell_bboxes=[BBox(x0=0, y0=0, x1=40, y1=20)],
        )[0]

        assert t.geometry_status == "low_confidence"
        assert "bbox_count_mismatch" in t.geometry_warnings
        assert t.geometry_confidence < 1.0

    def test_severe_bbox_row_conflict_is_marked(self):
        html = (
            "<table>"
            "<tr><td>A</td><td>B</td></tr>"
            "<tr><td>C</td><td>D</td></tr>"
            "<tr><td>E</td><td>F</td></tr>"
            "</table>"
        )
        bboxes = [
            BBox(x0=0, y0=0, x1=40, y1=20),
            BBox(x0=50, y0=0, x1=90, y1=20),
            BBox(x0=0, y0=0, x1=40, y1=20),
            BBox(x0=50, y0=0, x1=90, y1=20),
            BBox(x0=0, y0=0, x1=40, y1=20),
            BBox(x0=50, y0=0, x1=90, y1=20),
        ]

        t = parse_html_tables(html, cell_bboxes=bboxes)[0]

        assert t.geometry_status == "severe_conflict"
        assert "bbox_html_row_count_delta" in t.geometry_warnings

    def test_right_fragment_overflow_is_marked(self):
        html = "<table><tr><td>A</td><td>B</td><td>3</td><td>碎</td></tr></table>"
        t = parse_html_tables(
            html,
            cell_bboxes=[
                BBox(x0=0, y0=0, x1=40, y1=20),
                BBox(x0=50, y0=0, x1=90, y1=20),
            ],
        )[0]

        assert "right_fragment_overflow" in t.geometry_warnings

    def test_multiple_tables_use_each_tables_own_bbox_count(self):
        html = (
            "<table><tr><td>A</td><td>B</td></tr></table>"
            "<table><tr><td>C</td><td>D</td></tr></table>"
        )
        tables = parse_html_tables(
            html,
            cell_bboxes=[
                BBox(x0=0, y0=0, x1=40, y1=20),
                BBox(x0=50, y0=0, x1=90, y1=20),
                BBox(x0=0, y0=40, x1=40, y1=60),
                BBox(x0=50, y0=40, x1=90, y1=60),
            ],
        )

        assert [table.geometry_status for table in tables] == ["consistent", "consistent"]
        assert all("bbox_count_mismatch" not in table.geometry_warnings for table in tables)
        assert [table.bbox_cell_count for table in tables] == [2, 2]

    def test_invalid_span_attributes_fall_back_to_one(self):
        html = (
            '<table><tr><td colspan="">A</td><td rowspan="bad">B</td></tr>'
            '<tr><td colspan="0">C</td><td rowspan="-2">D</td></tr></table>'
        )

        table = parse_html_tables(html)[0]

        assert table.col_count == 2
        assert [[cell.text for cell in row.cells] for row in table.rows] == [["A", "B"], ["C", "D"]]
