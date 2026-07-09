from __future__ import annotations

from app.models import BBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.evidence_locator import EvidenceLocator
from app.services.page_diff import PageDiffConsolidator


def _block(block_id: str, page_no: int, text: str, bbox: BBox, block_type: str = "text") -> TextBlock:
    return TextBlock(block_id=block_id, page_no=page_no, text=text, bbox=bbox, block_type=block_type)


def _page(page_no: int, blocks: list[TextBlock]) -> Page:
    return Page(page_no=page_no, width=600, height=800, blocks=blocks)


def _document(pages: list[Page]) -> Document:
    return Document(filename="test.pdf", path="test.pdf", page_count=len(pages), pages=pages)


def _base_page() -> Page:
    return _page(
        1,
        [
            _block("p1_t1", 1, "第一条 合同范围", BBox(x0=60, y0=80, x1=260, y1=110)),
            _block("p1_t2", 1, "双方约定按照合同正文履行交付和验收义务。", BBox(x0=60, y0=125, x1=520, y1=155)),
        ],
    )


def _added_page(page_no: int, title: str) -> Page:
    return _page(
        page_no,
        [
            _block(f"p{page_no}_title", page_no, title, BBox(x0=80, y0=70, x1=440, y1=100), "paragraph_title"),
            _block(
                f"p{page_no}_table",
                page_no,
                "\n".join(
                    [
                        "序号 名称 型号 单位 数量 单价 总价",
                        "1 主控设备 SP-100 台 2 10000 20000",
                        "2 通讯设备 SP-200 台 1 5000 5000",
                        "3 软件模块 SP-300 套 1 8000 8000",
                        "合计 33000",
                    ]
                ),
                BBox(x0=60, y0=130, x1=540, y1=560),
                "table",
            ),
        ],
    )


def _same_added_content_on_page(page_no: int) -> Page:
    return _added_page(page_no, "设备清单")


def _evidence(page_no: int, bbox: BBox, diff_type: str) -> EvidenceBox:
    return EvidenceBox(
        page_no=page_no,
        bbox=bbox,
        method="table_cell",
        text="table",
        highlight_type=diff_type,
        confidence=0.9,
        evidence_quality="HIGH",
    )


def test_full_page_add_promotes_pages_and_suppresses_duplicate_table_diff() -> None:
    original = _document([_base_page()])
    compare = _document([_base_page(), _added_page(2, "设备报价明细"), _added_page(3, "软件报价明细")])
    table_diff = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="table",
        title="表格：标的物",
        compare_text="新增表格",
        compare_evidence=[
            _evidence(2, BBox(x0=60, y0=130, x1=540, y1=560), "ADD"),
            _evidence(3, BBox(x0=60, y0=130, x1=540, y1=560), "ADD"),
        ],
    )

    result = PageDiffConsolidator().consolidate(original, compare, [table_diff])

    assert [diff.source_type for diff in result] == ["page", "page"]
    assert [diff.diff_type for diff in result] == ["ADD", "ADD"]
    assert [evidence.page_no for diff in result for evidence in diff.compare_evidence] == [2, 3]
    assert all(diff.readable_change.startswith("整页新增") for diff in result)


def test_same_page_table_add_remains_table_diff() -> None:
    original = _document([_base_page()])
    compare = _document([
        _page(
            1,
            [
                *_base_page().blocks,
                _block("p1_table", 1, "新增设备 SP-100 数量 1 金额 10000", BBox(x0=60, y0=220, x1=540, y1=320), "table"),
            ],
        )
    ])
    table_diff = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="table",
        title="表格：标的物",
        compare_text="新增设备 SP-100 数量 1 金额 10000",
        compare_evidence=[_evidence(1, BBox(x0=60, y0=220, x1=540, y1=320), "ADD")],
    )

    result = PageDiffConsolidator().consolidate(original, compare, [table_diff])

    assert result == [table_diff]


def test_full_page_delete_promotes_page_delete() -> None:
    original = _document([_base_page(), _added_page(2, "设备清单")])
    compare = _document([_base_page()])
    table_diff = DiffItem(
        diff_id="D001",
        diff_type="DELETE",
        source_type="table",
        title="表格：标的物",
        original_text="删除表格",
        original_evidence=[_evidence(2, BBox(x0=60, y0=130, x1=540, y1=560), "DELETE")],
    )

    result = PageDiffConsolidator().consolidate(original, compare, [table_diff])

    assert len(result) == 1
    assert result[0].source_type == "page"
    assert result[0].diff_type == "DELETE"
    assert result[0].original_evidence[0].page_no == 2


def test_full_page_add_keeps_seal_diff() -> None:
    added = _added_page(2, "设备清单")
    added.blocks.append(_block("p2_seal", 2, "公司印章", BBox(x0=390, y0=500, x1=520, y1=640), "seal"))
    original = _document([_base_page()])
    compare = _document([_base_page(), added])
    table_diff = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="table",
        title="表格：标的物",
        compare_text="新增表格",
        compare_evidence=[_evidence(2, BBox(x0=60, y0=130, x1=540, y1=560), "ADD")],
    )
    seal_diff = DiffItem(
        diff_id="D002",
        diff_type="ADD",
        source_type="seal",
        title="印章区域",
        compare_text="公司印章",
        compare_evidence=[_evidence(2, BBox(x0=390, y0=500, x1=520, y1=640), "ADD")],
    )

    result = PageDiffConsolidator().consolidate(original, compare, [table_diff, seal_diff])

    assert [diff.source_type for diff in result] == ["seal", "page"]
    assert result[0].title == "印章区域"
    assert result[1].source_type == "page"


def test_page_delete_is_blocked_when_page_has_matched_modify_evidence() -> None:
    original = _document([_base_page(), _added_page(2, "设备清单")])
    compare = _document([_base_page(), _same_added_content_on_page(3)])
    table_delete = DiffItem(
        diff_id="D001",
        diff_type="DELETE",
        source_type="table",
        title="表格：标的物",
        original_text="删除表格",
        original_evidence=[_evidence(2, BBox(x0=60, y0=130, x1=540, y1=560), "DELETE")],
    )
    matched_modify = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        source_type="clause",
        title="标的物",
        original_text="原页内容",
        compare_text="对比页内容",
        original_evidence=[_evidence(2, BBox(x0=60, y0=570, x1=540, y1=620), "MODIFY")],
        compare_evidence=[_evidence(3, BBox(x0=60, y0=570, x1=540, y1=620), "MODIFY")],
    )

    result = PageDiffConsolidator().consolidate(original, compare, [table_delete, matched_modify])

    assert [diff.source_type for diff in result] == ["table", "clause"]
    assert all("PAGE_LEVEL_CHANGE" not in diff.structural_flags for diff in result)


def test_page_region_evidence_does_not_conflict_with_nested_text_evidence() -> None:
    page_diff = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="page",
        title="整页新增：第2页",
        compare_text="新增整页正文",
        compare_evidence=[
            EvidenceBox(
                page_no=2,
                bbox=BBox(x0=60, y0=70, x1=540, y1=560),
                method="page_region",
                text="新增整页正文",
                highlight_type="ADD",
            )
        ],
    )
    nested_diff = DiffItem(
        diff_id="D002",
        diff_type="ADD",
        source_type="clause",
        title="新增条款",
        compare_text="新增整页正文",
        compare_evidence=[
            EvidenceBox(
                page_no=2,
                bbox=BBox(x0=80, y0=90, x1=500, y1=110),
                method="char_exact",
                text="新增整页正文",
                highlight_type="ADD",
            )
        ],
    )

    result = EvidenceLocator().locate([page_diff, nested_diff], [], [])

    assert len(result[0].compare_evidence) == 1
    assert result[0].compare_evidence[0].method == "page_region"
    assert len(result[1].compare_evidence) == 1


def test_consolidate_preserves_existing_diff_ids_when_no_page_diff_is_added() -> None:
    original = _document([_base_page()])
    compare = _document([_base_page()])
    signing_diff = DiffItem(
        diff_id="D011",
        diff_type="MODIFY",
        source_type="signing_region",
        title="签署区（第13页）",
        original_text="签字日期：年月日",
        compare_text="签字日期：2026年05月22日",
        original_evidence=[_evidence(1, BBox(x0=80, y0=80, x1=520, y1=560), "MODIFY")],
        compare_evidence=[_evidence(1, BBox(x0=80, y0=80, x1=520, y1=560), "MODIFY")],
    )

    result = PageDiffConsolidator().consolidate(original, compare, [signing_diff])

    assert [diff.diff_id for diff in result] == ["D011"]
    assert result[0].source_type == "signing_region"


def test_page_diff_uses_next_available_id_without_renumbering_survivors() -> None:
    original = _document([_base_page()])
    compare = _document([_base_page(), _added_page(2, "设备报价明细")])
    table_diff = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="table",
        title="表格：标的物",
        compare_text="新增表格",
        compare_evidence=[_evidence(2, BBox(x0=60, y0=130, x1=540, y1=560), "ADD")],
    )
    signing_diff = DiffItem(
        diff_id="D011",
        diff_type="MODIFY",
        source_type="signing_region",
        title="签署区（第13页）",
        original_text="签字日期：年月日",
        compare_text="签字日期：2026年05月22日",
        original_evidence=[_evidence(1, BBox(x0=80, y0=80, x1=520, y1=560), "MODIFY")],
        compare_evidence=[_evidence(1, BBox(x0=80, y0=80, x1=520, y1=560), "MODIFY")],
    )

    result = PageDiffConsolidator().consolidate(original, compare, [table_diff, signing_diff])

    assert [(diff.diff_id, diff.source_type) for diff in result] == [
        ("D011", "signing_region"),
        ("D012", "page"),
    ]
