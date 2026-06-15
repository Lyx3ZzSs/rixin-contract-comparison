from app.models import BBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.signature_compare import SignatureComparator


def _make_doc(blocks: list[TextBlock]) -> Document:
    pages_by_no: dict[int, list[TextBlock]] = {}
    for block in blocks:
        pages_by_no.setdefault(block.page_no, []).append(block)
    pages = [
        Page(page_no=page_no, width=595, height=842, blocks=page_blocks)
        for page_no, page_blocks in sorted(pages_by_no.items())
    ]
    return Document(filename="test.pdf", path="test.pdf", page_count=len(pages), pages=pages)


def _table_block(block_id: str, html: str, page_no: int = 2) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=html,
        raw_html=html,
        bbox=BBox(x0=50, y0=100, x1=540, y1=500),
        block_type="table",
    )


def _signature_table() -> str:
    return (
        "<table>"
        "<tr><td>供 方</td><td>需 方</td></tr>"
        "<tr><td>单位名称（章）：国能日新科技股份有限公司 单位地址：北京市海淀区西三旗建材城中路</td>"
        "<td>单位名称(章）：斯美能源科技(青海)有限公司 单位地址：青海省西宁市城北区宁张路44号西宁</td></tr>"
        "<tr><td>27号1幢2层227号</td><td>创业孵化基地1号楼0814室</td></tr>"
        "<tr><td>法人代表：雍正</td><td>法人代表或授权委托人：</td></tr>"
        "<tr><td>委托代理人：</td><td>(签字)</td></tr>"
        "<tr><td>电 话：010-83458100</td><td>电话：18097182156</td></tr>"
        "</table>"
    )


def _merged_signature_table() -> str:
    return (
        "<table>"
        "<tr><td>供 方 技照</td><td>需 方 司</td></tr>"
        "<tr><td colspan='2'>宁</td></tr>"
        "<tr><td colspan='2'>电 话：010-83458100 电话：18097182156 传</td></tr>"
        "<tr><td colspan='2'>真：010-83458107 传真：</td></tr>"
        "</table>"
    )


def _split_value_signature_table(value: str = "张三") -> str:
    return (
        "<table>"
        "<tr><td colspan='2'>签字页</td></tr>"
        "<tr><td>法定代表人：</td><td>李四</td></tr>"
        f"<tr><td>授权委托人：</td><td>{value}</td></tr>"
        "<tr><td>统一社会信用代码：</td><td>911101086723891430</td></tr>"
        "<tr><td>邮箱：</td><td>contact@example.com</td></tr>"
        "</table>"
    )


def _continued_signature_table(address_suffix: str = "27号1幢2层227号") -> str:
    return (
        "<table>"
        "<tr><td>供 方</td><td>需 方</td></tr>"
        "<tr><td>单位地址：北京市海淀区西三旗建材城中路</td><td>单位地址：青海省西宁市城北区宁张路44号西宁</td></tr>"
        f"<tr><td>{address_suffix}</td><td>创业孵化基地1号楼0814室</td></tr>"
        "<tr><td>开户银行：招商银行北京大屯路</td><td>开户银行：招商银行股份有限公司西宁生物园区</td></tr>"
        "<tr><td>支行</td><td>支行</td></tr>"
        "</table>"
    )


def test_signature_comparator_gates_unreliable_signature_extraction() -> None:
    original = _make_doc([_table_block("o1", _signature_table())])
    compare = _make_doc([_table_block("c1", _merged_signature_table())])

    result = SignatureComparator().compare(original, compare)

    assert len(result.diffs) == 1
    assert result.diffs[0].title == "签字页：抽取质量复核"
    assert result.diffs[0].match_method == "signature_extraction_quality_gate"
    assert "SIGNATURE_EXTRACTION_UNRELIABLE" in result.diffs[0].review_flags
    assert not any("(签字)" in diff.original_text for diff in result.diffs)
    assert all(diff.source_type == "signature" for diff in result.diffs)
    assert all("SIGNATURE_SECTION_REVIEW" in diff.review_flags for diff in result.diffs)
    debug_payload = result.to_debug_payload()
    assert debug_payload["metrics"]["original_candidate_count"] > 0
    assert debug_payload["metrics"]["signature_diff_count"] == len(result.diffs)
    assert debug_payload["metrics"]["compare_extraction_unreliable"] is True
    assert any(
        candidate["reject_reason"] == "pending_label_without_value"
        for candidate in debug_payload["rejected_candidates"]
    )


def test_signature_comparator_extracts_split_label_value_cells() -> None:
    original = _make_doc([_table_block("o1", _split_value_signature_table("张三"))])
    compare = _make_doc([_table_block("c1", _split_value_signature_table("王五"))])

    result = SignatureComparator().compare(original, compare)

    by_title = {diff.title: diff for diff in result.diffs}
    assert by_title["签字页：左栏-授权代表"].diff_type == "MODIFY"
    assert by_title["签字页：左栏-授权代表"].original_text == "张三"
    assert by_title["签字页：左栏-授权代表"].compare_text == "王五"
    assert not any(diff.title == "签字页：左栏-统一社会信用代码" for diff in result.diffs)
    assert not any(diff.title == "签字页：左栏-邮箱" for diff in result.diffs)
    debug_payload = result.to_debug_payload()
    assert debug_payload["metrics"]["accepted_candidate_count"] >= 4
    assert any(candidate["status"] == "accepted" for candidate in debug_payload["original_candidates"])


def test_signature_comparator_continues_address_and_bank_rows() -> None:
    original = _make_doc([_table_block("o1", _continued_signature_table("27号1幢2层227号"))])
    compare = _make_doc([_table_block("c1", _continued_signature_table("27号1幢2层228号"))])

    result = SignatureComparator().compare(original, compare)

    by_title = {diff.title: diff for diff in result.diffs}
    assert by_title["签字页：供方-单位地址"].diff_type == "MODIFY"
    assert "227号" in by_title["签字页：供方-单位地址"].original_text
    assert "228号" in by_title["签字页：供方-单位地址"].compare_text
    assert "SIGNATURE_CONTINUED_FIELD" in by_title["签字页：供方-单位地址"].review_flags
    bank_fields = [field for field in result.original_fields if field.field_key == "bank"]
    assert bank_fields and bank_fields[0].field_value.endswith("支行")


def test_signature_comparator_rejects_label_fragments_as_values() -> None:
    original = _make_doc([_table_block("o1", _signature_table())])
    compare = _make_doc([
        _table_block(
            "c1",
            (
                "<table>"
                "<tr><td>供 方</td><td>需 方</td></tr>"
                "<tr><td>传真：开户银 帐 税</td><td>传真：</td></tr>"
                "<tr><td>开户银行：招商银行北京大屯路支行</td><td>开户银行：招商银行股份有限公司西宁生物园区</td></tr>"
                "<tr><td></td><td>支行</td></tr>"
                "</table>"
            ),
        )
    ])

    result = SignatureComparator().compare(original, compare)

    assert not any(field.field_key == "fax" and field.field_value == "开户银 帐 税" for field in result.compare_fields)
    assert any(
        candidate["reject_reason"] == "label_fragment_value"
        for candidate in result.to_debug_payload()["compare_candidates"]
    )


def test_signature_comparator_reports_company_stamp_modify() -> None:
    original = _make_doc([
        TextBlock(
            block_id="o1",
            page_no=1,
            text="乙方：【国能日新科技股份有限公司】（盖章）",
            bbox=BBox(x0=50, y0=300, x1=300, y1=330),
            block_type="text",
            block_role="signature",
        )
    ])
    compare = _make_doc([
        TextBlock(
            block_id="c1",
            page_no=1,
            text="乙方：【南京瑞尚电力科技有限公司】（盖章）",
            bbox=BBox(x0=50, y0=300, x1=300, y1=330),
            block_type="text",
            block_role="signature",
        )
    ])

    result = SignatureComparator().compare(original, compare)

    assert len(result.diffs) == 1
    assert result.diffs[0].diff_type == "MODIFY"
    assert result.diffs[0].title == "签字页：乙方-盖章主体"
    assert result.diffs[0].original_text == "国能日新科技股份有限公司"
    assert result.diffs[0].compare_text == "南京瑞尚电力科技有限公司"


def test_signature_comparator_marks_position_inferred_role() -> None:
    original = _make_doc([_table_block("o1", _split_value_signature_table("张三"))])
    compare = _make_doc([_table_block("c1", _split_value_signature_table("王五"))])

    result = SignatureComparator().compare(original, compare)

    diff = {diff.title: diff for diff in result.diffs}["签字页：左栏-授权代表"]
    assert "SIGNATURE_ROLE_INFERRED_BY_POSITION" in diff.review_flags


def test_signature_comparator_uses_higher_priority_winner_for_same_field() -> None:
    original = _make_doc([
        TextBlock(
            block_id="o_text",
            page_no=1,
            text="乙方：【国能日新科技股份有限公司】（盖章）",
            bbox=BBox(x0=50, y0=300, x1=300, y1=330),
            block_type="text",
            block_role="signature",
        ),
        TextBlock(
            block_id="o_seal",
            page_no=1,
            text="南京瑞尚电力科技有限公司",
            bbox=BBox(x0=60, y0=290, x1=310, y1=340),
            block_type="seal",
        ),
    ])
    compare = _make_doc([
        TextBlock(
            block_id="c_text",
            page_no=1,
            text="乙方：【国能日新科技股份有限公司】（盖章）",
            bbox=BBox(x0=50, y0=300, x1=300, y1=330),
            block_type="text",
            block_role="signature",
        )
    ])

    result = SignatureComparator().compare(original, compare)

    assert [field.field_value for field in result.original_fields if field.field_key == "seal_text"] == [
        "南京瑞尚电力科技有限公司"
    ]
    assert any("SIGNATURE_FIELD_SOURCE_CONFLICT" in field.review_flags for field in result.original_fields)
    assert any(
        candidate["reject_reason"] == "field_conflict_lower_priority"
        for candidate in result.to_debug_payload()["rejected_candidates"]
    )


def test_signature_comparator_suppresses_covered_table_diff() -> None:
    signature_diff = DiffItem(
        diff_id="D010",
        diff_type="DELETE",
        source_type="signature",
        section_type="signature",
        title="签字页：供方-法人代表",
        original_text="雍正",
        original_evidence=[
            EvidenceBox(
                page_no=2,
                bbox=BBox(x0=60, y0=300, x1=160, y1=320),
                method="signature_table_cell",
                text="雍正",
                highlight_type="DELETE",
            )
        ],
    )
    table_diff = DiffItem(
        diff_id="D002",
        diff_type="DELETE",
        source_type="table",
        title="表格字段：联系人",
        original_text="法人代表：雍正 | 法人代表或授权委托人：",
        original_evidence=[
            EvidenceBox(
                page_no=2,
                bbox=BBox(x0=50, y0=290, x1=180, y1=330),
                method="table_cell",
                text="法人代表：雍正",
                highlight_type="DELETE",
            )
        ],
    )

    comparator = SignatureComparator()

    assert comparator.suppressed_table_diff_ids([table_diff], [signature_diff]) == ["D002"]
    assert comparator.remove_suppressed_table_diffs([table_diff], [signature_diff]) == []


def test_signature_comparator_suppresses_covered_seal_diff() -> None:
    signature_diff = DiffItem(
        diff_id="D010",
        diff_type="MODIFY",
        source_type="signature",
        section_type="signature",
        title="签字页：乙方-盖章主体",
        original_text="国能日新科技股份有限公司",
        compare_text="南京瑞尚电力科技有限公司",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=60, y0=300, x1=180, y1=340),
                method="signature_seal",
                text="国能日新科技股份有限公司",
                highlight_type="MODIFY",
            )
        ],
    )
    seal_diff = DiffItem(
        diff_id="D003",
        diff_type="MODIFY",
        source_type="seal",
        title="印章区域（第1页）",
        original_text="国能日新科技股份有限公司",
        compare_text="南京瑞尚电力科技有限公司",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=50, y0=290, x1=200, y1=360),
                method="seal_region",
                text="国能日新科技股份有限公司",
                highlight_type="MODIFY",
            )
        ],
    )

    comparator = SignatureComparator()

    assert comparator.suppressed_seal_diff_ids([seal_diff], [signature_diff]) == ["D003"]
    assert comparator.remove_suppressed_non_body_diffs([], [seal_diff], [signature_diff]) == ([], [])
