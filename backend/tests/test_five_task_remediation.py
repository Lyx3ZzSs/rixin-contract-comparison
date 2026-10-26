"""Regressions for provenance/order, page absence and recognition admission."""

from types import SimpleNamespace
from app.models import BBox, CharBox, TextBlock, DiffItem, EvidenceBox
from app.services.extractors.contract_v3.tables import _publish
from app.services.extractors.contract_v3.artifact import to_page
from app.services.signing_region.extractor import SigningRegionExtractor
from app.services.table_compare.facade import TableComparator
from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus
from app.services.diff_quality import DiffQualityProcessor


def test_column_major_provider_publishes_one_canonical_cell_order():
    cells = [
        dict(row=r, column=c, rowspan=1, colspan=1, bbox=[c * 100, r * 50, (c + 1) * 100, (r + 1) * 50])
        for c in range(2)
        for r in range(2)
    ]
    tokens = [
        dict(
            id=str(i),
            text=str(i),
            bbox=[c["bbox"][0] + 10, c["bbox"][1] + 10, c["bbox"][0] + 20, c["bbox"][1] + 20],
            block_id=str(i),
        )
        for i, c in enumerate(cells)
    ]
    artifact = dict(blocks=[dict(region_id="t", block_id="0")])
    result = _publish(artifact, dict(region_id="t", bbox=[0, 0, 200, 100]), cells, tokens, "native_pdf")
    b = artifact["blocks"][0]
    assert result["status"] == "CONSISTENT"
    assert [(c["row"], c["column"]) for c in b["table_cells"]] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert [c["text"] for c in b["table_cells"]] == ["0", "2", "1", "3"]
    assert b["table_cell_bboxes"] == [c["bbox"] for c in b["table_cells"]]
    assert b["raw_html"].index(">2<") < b["raw_html"].index(">1<")


def test_ocr_adapter_preserves_low_recognition_confidence():
    a = dict(
        page_no=1,
        width=200,
        height=100,
        regions=[],
        native_chars=[],
        blocks=[
            dict(
                block_id="x",
                text="2",
                bbox=[1, 1, 10, 10],
                resolved_block_type="text",
                source="PP-OCRv6",
                reading_order=0,
                reason="ocr",
                confidence=0.96,
                raw_ocr_score=0.61,
                ocr_char_provenance=[dict(char="2", bbox=[1, 1, 10, 10], text_index=0)],
            )
        ],
    )
    b = to_page(a).blocks[0]
    assert b.confidence == b.char_boxes[0].confidence == 0.61


def test_signing_table_collects_all_source_chars_without_duplicate_fragments():
    texts = ["单位名称：甲公司", "单 位 地 址：北京市", "法人代表：雍正"]
    blocks = []
    for row, text in enumerate(texts):
        chars = [
            CharBox(
                char=c,
                page_no=1,
                text_index=i,
                bbox=BBox(x0=10 + i * 7, y0=10 + row * 20, x1=17 + i * 7, y1=20 + row * 20),
            )
            for i, c in enumerate(text)
        ]
        blocks.append(
            TextBlock(
                block_id=f"b{row}",
                page_no=1,
                text=text,
                bbox=BBox(x0=10, y0=10 + row * 20, x1=150, y1=20 + row * 20),
                char_boxes=chars,
                layout_block_id="table",
            )
        )
    first = blocks[0]
    first.raw_html = "<table><tr><td>" + "".join(texts) + "</td></tr></table>"
    first.table_cell_bboxes = [[0, 0, 160, 80]]
    first.table_cells = [dict(token_refs=[dict(block_id=b.block_id) for b in blocks])]
    result = SigningRegionExtractor._expand_table_field_blocks(blocks)
    assert [b.text for b in result] == ["单位名称：甲公司", "单位地址：北京市", "法人代表：雍正"]
    address = result[1]
    start = address.text.index("北京市")
    bbox = SigningRegionExtractor._field_bbox(address, start, len(address.text))
    assert bbox.y0 == 30 and bbox.x0 > 50
    assert all(b.source == "signing_table_cell" for b in result)


def test_inserted_page_is_absent_even_when_counterpart_same_number_has_table():
    service = TableComparator()
    entry = PageAlignmentEntry(
        status=PageAlignmentStatus.INSERTED,
        original_pages=(),
        compare_pages=(8, 9),
        alignment_score=1.0,
        anchors=[],
        reason="inserted",
    )
    service._matcher.page_alignment = PageAlignmentResult(entries=[entry])
    d = DiffItem(
        diff_id="d",
        diff_type="ADD",
        source_type="table",
        compare_evidence=[EvidenceBox(page_no=p, bbox=BBox(x0=0, y0=0, x1=100, y1=100)) for p in (8, 9)],
    )
    service._mark_aligned_unmatched_table_candidate(d, 8, deleted=False)
    assert d.diff_type == "ADD"
    assert d.match_score_details["counterpart_table_state"] == "PAGE_ABSENT"


def test_geometry_confidence_does_not_confirm_unknown_ocr_amount():
    p = DiffQualityProcessor()
    p.evidence_consensus = SimpleNamespace(
        assess=lambda *args: SimpleNamespace(verdict="UNKNOWN", reason="NOT_EQUIVALENT", sources=[], detail={})
    )
    d = DiffItem(
        diff_id="d",
        diff_type="MODIFY",
        source_type="clause",
        original_text="金额100元",
        compare_text="金额700元",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=0, y0=0, x1=100, y1=20),
                method="char_exact",
                confidence=0.98,
                text_confidence=0.65,
                text="100",
            )
        ],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=0, y0=0, x1=100, y1=20),
                method="char_exact",
                confidence=0.98,
                text_confidence=0.65,
                text="700",
            )
        ],
    )
    p._apply_admission_status([d], [], original_document=None, compare_document=None)
    assert d.match_score_details["change_decision"] == "NEEDS_REVIEW"


def test_parser_uses_cell_coordinates_instead_of_parallel_array_order():
    from app.services.table_compare.html_parser import parse_html_tables

    cells = [dict(row=0, column=1, bbox=[100, 0, 200, 40]), dict(row=0, column=0, bbox=[0, 0, 100, 40])]
    table = parse_html_tables("<table><tr><td>甲</td><td>乙</td></tr></table>", cell_records=cells)[0]
    assert [(c.text, c.bbox.x0) for c in table.rows[0].cells] == [("甲", 0), ("乙", 100)]


def test_label_fragments_do_not_cross_party_columns():
    def block(id, text, x):
        return TextBlock(block_id=id, page_no=1, text=text, bbox=BBox(x0=x, y0=100, x1=x + 15, y1=112))

    tax = block("tax", "税", 65)
    left = block("l", "号：123", 132)
    right = block("r", "号：456", 380)
    result = SigningRegionExtractor._join_field_label_fragments([left, right], [tax, left, right], 300)
    assert [b.text for b in result] == ["税号：123", "号：456"]


def test_unchanged_page_start_requires_both_sides_and_location():
    from app.models import Document, Page
    from app.services.source_consensus import unchanged_page_start_attached_to_clause

    def doc(text, y):
        return Document(
            filename="x",
            path="x.pdf",
            page_count=1,
            pages=[
                Page(
                    page_no=3,
                    width=600,
                    height=800,
                    blocks=[TextBlock(block_id="b", page_no=3, text=text, bbox=BBox(x0=60, y0=y, x1=140, y1=y + 15))],
                )
            ],
        )

    diff = DiffItem(
        diff_id="d",
        diff_type="MODIFY",
        source_type="clause",
        original_text="双方签字生效。",
        compare_text="双方签字生效。\n设备清单",
    )
    assert unchanged_page_start_attached_to_clause(diff, doc("设备清单", 60), doc("设备清单", 65))
    assert not unchanged_page_start_attached_to_clause(diff, doc("清单附件", 60), doc("设备清单", 65))
    assert not unchanged_page_start_attached_to_clause(diff, doc("设备清单", 500), doc("设备清单", 65))


def test_same_date_at_distinct_locations_is_not_deduplicated():
    from app.services.pipeline_stages import _reconcile_visual_signing_date_diffs

    visual = DiffItem(
        diff_id="v",
        diff_type="ADD",
        source_type="table",
        title="签署日期",
        compare_text="2026年4月21日",
        match_score_details={"object_type": "HANDWRITTEN_DATE"},
        compare_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=50, y0=100, x1=200, y1=120))],
    )
    date = visual.model_copy(deep=True)
    date.diff_id = "d"
    date.source_type = "metadata"
    date.compare_evidence[0].bbox = BBox(x0=50, y0=600, x1=200, y1=620)
    assert len(_reconcile_visual_signing_date_diffs([visual, date])[0]) == 2


def test_valid_but_suspicious_ocr_date_is_reread_with_bounded_work(tmp_path):
    from tests.test_golden_v3_root_repairs import DateReader, block, doc, pdf
    from app.services.cover.date_ocr import CoverDateOCR

    path = pdf(tmp_path, "date.pdf", lambda page: page.insert_text((250, 670), "2026 / 4 / 21"))
    value = block("2026年4月2日", source="PP-OCRv6", confidence=0.99)
    diagnostics = CoverDateOCR(DateReader(["2026年4月21日"] * 3)).enrich(doc(path, [value]))
    assert value.text == "2026年4月21日"
    assert len(diagnostics[0]["readings"]) == 3
    assert value.source_evidence[0].raw_text == "2026年4月2日"
    assert not value.char_boxes


def test_identifier_disagreement_keeps_original_and_review_marker(tmp_path):
    from tests.test_golden_v3_root_repairs import DateReader, block, doc, pdf
    from app.services.cover.date_ocr import CoverIdentifierOCR

    path = pdf(tmp_path, "number.pdf", lambda page: page.insert_text((100, 100), "GN-20260421-001"))
    value = block("GN-20260421-001", BBox(x0=100, y0=80, x1=300, y1=120), source="PP-OCRv6", confidence=0.7)
    diagnostics = CoverIdentifierOCR(DateReader(["GN-20260421-001", "GN-20260421-007", "GN-20260421-001"])).enrich(
        doc(path, [value])
    )
    assert value.text == "GN-20260421-001"
    assert diagnostics[0]["status"] == "NEEDS_REVIEW"
    assert "UNRESOLVED_COVER_IDENTIFIER" in value.semantic_reasons


def test_reclassified_table_and_visual_date_share_one_cover_event():
    from app.services.pipeline_stages import _reconcile_visual_signing_date_diffs

    roi = BBox(x0=50, y0=500, x1=250, y1=530)

    def visual(id):
        return DiffItem(
            diff_id=id,
            diff_type="ADD",
            source_type="metadata",
            title="签署日期",
            compare_text="2026年4月21日",
            match_score_details={"object_type": "HANDWRITTEN_DATE"},
            compare_evidence=[EvidenceBox(page_no=1, bbox=roi, method="whole_page_visual_object")],
        )

    cover = DiffItem(
        diff_id="cover",
        diff_type="MODIFY",
        source_type="metadata",
        title="封面字段：签订日期",
        original_text="年月日",
        compare_text="2026年4月21日",
        compare_evidence=[EvidenceBox(page_no=1, bbox=roi, method="cover_metadata")],
    )
    result, remap = _reconcile_visual_signing_date_diffs([cover, visual("table"), visual("WHOLE-PAGE-1")])
    assert [d.diff_id for d in result] == ["cover"]
    assert remap == {"table": "cover", "WHOLE-PAGE-1": "cover"}
    assert len(cover.match_score_details["merged_date_observations"]) == 2


def test_one_visual_date_cannot_be_claimed_twice_by_table_and_cover():
    from app.services.pipeline_stages import _reconcile_visual_signing_date_diffs

    roi = BBox(x0=50, y0=500, x1=250, y1=530)
    visual = DiffItem(
        diff_id="WHOLE-PAGE-1",
        diff_type="ADD",
        source_type="signing_region",
        title="手写日期",
        compare_text="2026年4月21日",
        match_score_details={"object_type": "HANDWRITTEN_DATE"},
        compare_evidence=[EvidenceBox(page_no=1, bbox=roi, method="whole_page_visual_object")],
    )
    cover = DiffItem(
        diff_id="cover",
        diff_type="MODIFY",
        source_type="metadata",
        title="封面字段：签订日期",
        original_text="年月日",
        compare_text="2026年4月21日",
        compare_evidence=[EvidenceBox(page_no=1, bbox=roi, method="cover_metadata")],
    )
    table = DiffItem(
        diff_id="table",
        diff_type="ADD",
        source_type="metadata",
        title="签署日期",
        compare_text="签订日期2026年4月21日",
        match_score_details={"page_alignment": {"compare_pages": [1]}},
    )
    result, remap = _reconcile_visual_signing_date_diffs([table, visual, cover])
    assert [d.diff_id for d in result] == ["cover"]
    assert remap == {"WHOLE-PAGE-1": "cover", "table": "cover"}


def test_substantial_phrase_change_requires_source_characters_and_text_confidence():
    from app.models import Document, Page
    from app.services.text_evidence import EvidenceConsensusEvaluator

    def make(text, source):
        chars = [
            CharBox(
                char=c,
                page_no=1,
                text_index=i,
                confidence=0.99,
                bbox=BBox(x0=100 + i * 10, y0=200, x1=110 + i * 10, y1=215),
            )
            for i, c in enumerate(text)
        ]
        block = TextBlock(
            block_id=source,
            page_no=1,
            text=text,
            source=source,
            confidence=0.99,
            bbox=BBox(x0=100, y0=200, x1=100 + len(text) * 10, y1=215),
            char_boxes=chars,
        )
        document = Document(
            filename="x.pdf", path="x.pdf", page_count=1, pages=[Page(page_no=1, width=600, height=800, blocks=[block])]
        )
        evidence = EvidenceBox(
            page_no=1,
            text=text,
            bbox=block.bbox,
            char_boxes=chars,
            text_confidence=0.99,
            method="char_exact",
            confidence=0.98,
        )
        return document, evidence

    original, left = make("30天", "native_pdf")
    compare, right = make("在委托服务期限", "PP-OCRv6")
    diff = DiffItem(
        diff_id="d",
        diff_type="MODIFY",
        source_type="clause",
        original_text=left.text,
        compare_text=right.text,
        original_evidence=[left],
        compare_evidence=[right],
    )
    evaluator = EvidenceConsensusEvaluator()
    assert evaluator.assess(diff, original, compare).verdict == "DIFFERENT"
    right.text_confidence = 0.6
    assert evaluator.assess(diff, original, compare).verdict == "UNKNOWN"
    right.text_confidence = 0.99
    compare.pages[0].blocks[0].char_boxes = []
    assert evaluator.assess(diff, original, compare).verdict == "UNKNOWN"
