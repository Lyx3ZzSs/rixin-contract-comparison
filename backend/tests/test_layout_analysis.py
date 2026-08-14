from __future__ import annotations

import copy
from pathlib import Path

import fitz
import pytest

from app.config import Settings, settings
from app.models import (
    BBox,
    CompareTask,
    Document,
    LayoutQualityReport,
    Page,
    ParseWarningDetail,
    TextBlock,
)
from app.services.extractors.base import ExtractionResult
from app.services.extractors.ppstructure import PPStructureExtractor
from app.services.extractors.ppstructure_ocr_hybrid import PPStructureOCRHybridExtractor
from app.services.layout_analysis import PPStructureLayoutAdapter
from app.services.models.layout_detector import LayoutDetector
from app.services.pipeline import PipelineContext
from app.services.pipeline_stages import ExtractionStage
from app.services.reading_order import assign_page_reading_order


def _payload() -> dict:
    return {
        "result": {
            "dataInfo": {"pages": [{"width": 1000, "height": 2000}]},
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "parsing_res_list": [
                            {
                                "block_label": "paragraph_title",
                                "block_content": "Section title",
                                "block_bbox": [100, 100, 900, 200],
                            },
                            {
                                "block_label": "seal",
                                "block_content": "",
                                "block_bbox": [700, 1500, 900, 1800],
                            },
                            {
                                "block_label": "table",
                                "block_content": "<table><tr><td>A</td></tr></table>",
                                "block_bbox": [50, 400, 450, 800],
                            },
                            {
                                "block_label": "table",
                                "block_content": "<table><tr><td>B</td></tr></table>",
                                "block_bbox": [550, 400, 950, 800],
                            },
                            {
                                "block_label": "text",
                                "block_content": "missing bbox",
                            },
                        ],
                        "table_res_list": [
                            {"cell_box_list": [[600, 450, 900, 750]]},
                            {"cell_box_list": [[100, 450, 400, 750]]},
                        ],
                    }
                }
            ],
        }
    }


def _pdf(path: Path, width: float = 500, height: float = 1000) -> Path:
    doc = fitz.open()
    doc.new_page(width=width, height=height)
    doc.save(path)
    doc.close()
    return path


def test_layout_adapter_preserves_semantic_labels_and_non_text_regions() -> None:
    result = PPStructureLayoutAdapter().parse(_payload(), [(500, 1000)])

    assert [region.region_type for region in result.regions] == [
        "paragraph_title",
        "seal",
        "table",
        "table",
    ]
    assert result.regions[0].original_label == "paragraph_title"
    assert result.regions[1].text == ""
    assert result.regions[1].bbox == BBox(x0=350, y0=750, x1=450, y1=900)
    assert result.quality.invalid_bbox_count == 1
    assert result.quality.empty_region_count == 1
    assert result.quality.label_counts["paragraph_title"] == 1


def test_layout_adapter_keeps_vision_footnote_as_footnote() -> None:
    payload = _payload()
    payload["result"]["layoutParsingResults"][0]["prunedResult"]["parsing_res_list"].append(
        {
            "block_label": "vision_footnote",
            "block_content": "单位：元（人民币）",
            "block_bbox": [100, 300, 400, 350],
        }
    )

    result = PPStructureLayoutAdapter(mode="v3").parse(payload, [(500, 1000)])
    document = PPStructureLayoutAdapter(mode="v3").to_document(result, "sample.pdf")

    footnote = next(block for block in document.pages[0].blocks if block.text == "单位：元（人民币）")
    assert footnote.block_type == "footnote"
    assert footnote.flow_role == "note"


def test_layout_adapter_matches_multiple_table_cell_results_one_to_one() -> None:
    result = PPStructureLayoutAdapter().parse(_payload(), [(500, 1000)])
    tables = [region for region in result.regions if region.region_type == "table"]

    assert tables[0].table_cell_bboxes == [[50.0, 225.0, 200.0, 375.0]]
    assert tables[1].table_cell_bboxes == [[300.0, 225.0, 450.0, 375.0]]
    assert result.quality.table_cell_matched_count == 2
    assert result.quality.table_cell_unmatched_count == 0


def test_layout_adapter_never_uses_full_page_bbox_for_invalid_v2_region() -> None:
    result = PPStructureLayoutAdapter(mode="v2").parse(_payload(), [(500, 1000)])

    assert all(region.text != "missing bbox" for region in result.regions)
    assert all(region.bbox != BBox(x0=0, y0=0, x1=500, y1=1000) for region in result.regions)


def test_layout_adapter_legacy_mode_keeps_old_full_page_fallback() -> None:
    result = PPStructureLayoutAdapter(mode="legacy").parse(_payload(), [(500, 1000)])

    missing = next(region for region in result.regions if region.text == "missing bbox")
    assert missing.bbox == BBox(x0=0, y0=0, x1=500, y1=1000)
    assert not any(region.region_type == "seal" for region in result.regions)


def test_layout_detector_uses_shared_response_adapter() -> None:
    detector = LayoutDetector(base_url="https://layout.example")

    result = detector._parse_response(_payload(), [(500, 1000)])

    assert result.quality.invalid_bbox_count == 1
    assert any(region.original_label == "paragraph_title" for region in result.regions)


def test_ppstructure_shadow_mode_returns_legacy_document_and_v2_quality(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "sample.pdf")
    extractor = PPStructureExtractor(app_settings=Settings(layout_analysis_mode="shadow"))

    document = extractor.payload_to_document(_payload(), pdf)

    assert any(block.text == "missing bbox" for block in document.pages[0].blocks)
    assert extractor.last_layout is not None
    assert extractor.last_layout.quality.mode == "shadow"
    assert any(warning.code == "LAYOUT_SHADOW_DIFFERENCE" for warning in extractor.last_layout.quality.warnings)


def test_ppstructure_v3_shadow_keeps_v2_flow_and_records_v3_quality(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "sample.pdf")
    extractor = PPStructureExtractor(app_settings=Settings(layout_analysis_mode="v3_shadow"))

    document = extractor.payload_to_document(_payload(), pdf)

    assert all(not block.flow_role for block in document.pages[0].blocks)
    assert extractor.last_layout is not None
    assert extractor.last_layout.quality.parser_version == "v3"
    assert extractor.last_layout.quality.mode == "v3_shadow"
    assert any(warning.code == "LAYOUT_V3_SHADOW" for warning in extractor.last_layout.quality.warnings)


def test_layout_adapter_v3_assigns_flow_roles() -> None:
    adapter = PPStructureLayoutAdapter(mode="v3")
    layout = adapter.parse(_payload(), [(500, 1000)])

    document = adapter.to_document(layout, "sample.pdf")

    roles = {block.block_type: block.flow_role for block in document.pages[0].blocks}
    assert roles["paragraph_title"] == "heading"
    assert roles["seal"] == "non_text"
    assert roles["table"] == "table"


def test_reading_order_handles_spanning_title_and_two_columns() -> None:
    page = Page(
        page_no=1,
        width=600,
        height=800,
        blocks=[
            _block("right-2", 340, 200, 560, 230),
            _block("left-2", 40, 200, 260, 230),
            _block("title", 40, 20, 560, 60),
            _block("right-1", 340, 100, 560, 130),
            _block("left-1", 40, 100, 260, 130),
        ],
    )

    assign_page_reading_order(page)

    assert [block.block_id for block in sorted(page.blocks, key=lambda block: block.reading_order or 0)] == [
        "title",
        "left-1",
        "left-2",
        "right-1",
        "right-2",
    ]


def test_v3_reading_order_groups_margin_columns_and_aside() -> None:
    page = Page(
        page_no=1,
        width=600,
        height=800,
        blocks=[
            _block("footer", 200, 760, 400, 780, flow_role="margin"),
            _block("aside", 5, 180, 35, 220, flow_role="aside"),
            _block("right", 340, 100, 560, 130, flow_role="body"),
            _block("left-2", 40, 200, 260, 230, flow_role="body"),
            _block("left-1", 40, 100, 260, 130, flow_role="body"),
            _block("header", 200, 10, 400, 30, flow_role="margin"),
        ],
    )

    assign_page_reading_order(page)

    assert [block.block_id for block in sorted(page.blocks, key=lambda block: block.reading_order or 0)] == [
        "header",
        "left-1",
        "left-2",
        "aside",
        "right",
        "footer",
    ]


def test_v3_reading_order_keeps_figure_with_caption() -> None:
    page = Page(
        page_no=1,
        width=600,
        height=800,
        blocks=[
            _block("body", 40, 500, 560, 540, flow_role="body"),
            _block("figure", 100, 200, 500, 400, flow_role="non_text"),
            _block("caption", 100, 170, 500, 190, flow_role="caption"),
        ],
    )

    assign_page_reading_order(page)

    assert [block.block_id for block in sorted(page.blocks, key=lambda block: block.reading_order or 0)] == [
        "caption",
        "figure",
        "body",
    ]


def test_hybrid_keeps_structure_only_seal_and_assigns_reading_order() -> None:
    structure = ExtractionResult(
        document=Document(
            filename="scan.pdf",
            path="scan.pdf",
            page_count=1,
            pages=[
                Page(
                    page_no=1,
                    width=600,
                    height=800,
                    blocks=[
                        _block("p1_ppstructure_b1", 50, 100, 550, 160, "text", "正文"),
                        _block("p1_ppstructure_b2", 400, 500, 520, 620, "seal", ""),
                    ],
                )
            ],
        ),
        extractor_used="ppstructure",
        layout_quality=LayoutQualityReport(page_count=1, region_count=2),
    )
    ocr = ExtractionResult(
        document=Document(
            filename="scan.pdf",
            path="scan.pdf",
            page_count=1,
            pages=[
                Page(page_no=1, width=600, height=800, blocks=[_block("ocr", 60, 110, 300, 140, "ocr_line", "正文")])
            ],
        ),
        extractor_used="ppocrv5",
    )

    result = PPStructureOCRHybridExtractor(_StaticExtractor(structure), _StaticExtractor(ocr)).extract("scan.pdf")

    assert [block.block_type for block in result.document.pages[0].blocks] == ["text", "seal"]
    assert result.document.pages[0].blocks[1].source == "ppstructure_layout_only"
    assert all(block.reading_order is not None for block in result.document.pages[0].blocks)
    assert result.layout_quality is not None
    assert result.layout_quality.matched_ocr_block_count == 1


def test_v3_hybrid_classifies_ambiguous_meaningful_and_noise_matches() -> None:
    structure = ExtractionResult(
        document=Document(
            filename="scan.pdf",
            path="scan.pdf",
            page_count=1,
            pages=[
                Page(
                    page_no=1,
                    width=600,
                    height=800,
                    blocks=[
                        _block("s1", 40, 100, 560, 180, "text", "ambiguous target"),
                        _block("s2", 40, 100, 560, 180, "text", "ambiguous target"),
                    ],
                )
            ],
        ),
        extractor_used="ppstructure",
        layout_quality=LayoutQualityReport(parser_version="v3", mode="v3", page_count=1, region_count=2),
    )
    ocr = ExtractionResult(
        document=Document(
            filename="scan.pdf",
            path="scan.pdf",
            page_count=1,
            pages=[
                Page(
                    page_no=1,
                    width=600,
                    height=800,
                    blocks=[
                        _block("ambiguous", 60, 110, 300, 140, "ocr_line", "ambiguous target"),
                        _block("meaningful", 200, 400, 400, 430, "ocr_line", "important unmatched clause"),
                        _block("noise", 590, 500, 599, 510, "ocr_line", "#"),
                    ],
                )
            ],
        ),
        extractor_used="ppocrv5",
    )

    result = PPStructureOCRHybridExtractor(
        _StaticExtractor(structure),
        _StaticExtractor(ocr),
        app_settings=Settings(layout_analysis_mode="v3"),
    ).extract("scan.pdf")

    statuses = {block.block_id: block.layout_match_status for block in result.document.pages[0].blocks}
    assert statuses["ambiguous"] == "ambiguous"
    assert statuses["meaningful"] == "meaningful_unmatched"
    assert statuses["noise"] == "noise_unmatched"
    assert result.layout_quality is not None
    assert result.layout_quality.ambiguous_match_count == 1
    assert result.layout_quality.meaningful_unmatched_count == 1
    assert result.layout_quality.noise_unmatched_count == 1
    assert any(warning.code == "LAYOUT_AMBIGUOUS_MATCH" for warning in result.layout_quality.warnings)
    assert any(warning.code == "LAYOUT_MEANINGFUL_UNMATCHED" for warning in result.layout_quality.warnings)


def test_extraction_stage_persists_layout_quality_debug_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "storage"
    monkeypatch.setattr(settings, "storage_dir", storage)
    monkeypatch.setattr(settings, "tasks_dir", storage / "tasks")
    warning = ParseWarningDetail(
        code="LAYOUT_LOW_MATCH_RATE",
        message="layout match rate is low",
        source="layout_analysis",
    )
    result = ExtractionResult(
        document=Document(
            filename="sample.pdf",
            path="sample.pdf",
            page_count=1,
            pages=[Page(page_no=1, width=600, height=800, blocks=[_block("b1", 10, 10, 100, 30)])],
        ),
        extractor_used="ppstructure_ocr_hybrid",
        layout_quality=LayoutQualityReport(page_count=1, warnings=[warning]),
    )
    ctx = PipelineContext(
        task=CompareTask(task_id="TLAYOUT"),
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )

    ExtractionStage(extractor=_StaticExtractor(result)).execute(ctx)

    assert "layout_quality" in ctx.task.debug_artifact_paths
    assert Path(ctx.task.debug_artifact_paths["layout_quality"]).exists()
    assert any(item.code == "LAYOUT_LOW_MATCH_RATE" for item in ctx.task.parse_warning_details)


def test_hybrid_trusts_structure_delivery_date_when_ocr_misses_month_fragment() -> None:
    extractor = PPStructureOCRHybridExtractor(overlap_threshold=0.5)
    structure_block = _block(
        "p1_ppstructure_b18",
        80,
        560,
        290,
        575,
        text="交货日期：2026年月日交货",
    )
    children = [
        _block("p1_ppocrv5_b22", 85, 562, 188, 574, text="交货日期：2026年").model_copy(
            update={"layout_block_id": structure_block.block_id}
        ),
        _block("p1_ppocrv5_b23", 255, 562, 288, 575, text="且交货").model_copy(
            update={"layout_block_id": structure_block.block_id}
        ),
    ]

    result = extractor._consolidate_structure_text_blocks(
        children,
        {structure_block.block_id: structure_block},
        set(),
    )

    assert len(result) == 1
    assert result[0].text == "交货日期:2026年月日交货"
    assert result[0].source == "ppstructure_text"


def test_hybrid_repairs_day_confusion_in_structure_delivery_date() -> None:
    extractor = PPStructureOCRHybridExtractor(overlap_threshold=0.5)
    structure_block = _block(
        "p5_ppstructure_b19",
        80,
        560,
        290,
        575,
        text="交货日期：2026年月且交货",
    )
    children = [
        _block("p5_ppocrv5_b22", 85, 562, 188, 574, text="交货日期：2026年").model_copy(
            update={"layout_block_id": structure_block.block_id}
        ),
        _block("p5_ppocrv5_b23", 255, 562, 288, 575, text="且交货").model_copy(
            update={"layout_block_id": structure_block.block_id}
        ),
    ]

    result = extractor._consolidate_structure_text_blocks(
        children,
        {structure_block.block_id: structure_block},
        set(),
    )

    assert len(result) == 1
    assert result[0].text == "交货日期:2026年月日交货"
    assert result[0].source == "ppstructure_text"


def test_hybrid_trusts_structure_clause_marker_when_ocr_reads_ten_as_tu() -> None:
    extractor = PPStructureOCRHybridExtractor(overlap_threshold=0.5)
    structure_block = _block(
        "p2_ppstructure_b12",
        60,
        200,
        520,
        216,
        text="十二、本合同自双方签字盖章之日起生效。本合同1式4份",
    )
    children = [
        _block(
            "p2_ppocrv5_b7",
            62,
            201,
            518,
            215,
            text="土二、本合同自双方签字盖章之日起生效。本合同1式4份",
        ).model_copy(update={"layout_block_id": structure_block.block_id}),
    ]

    result = extractor._consolidate_structure_text_blocks(
        children,
        {structure_block.block_id: structure_block},
        set(),
    )

    assert len(result) == 1
    assert result[0].text == "十二、本合同自双方签字盖章之日起生效。本合同1式4份"
    assert result[0].source == "ppstructure_text"


def test_hybrid_trusts_merged_structure_clause_block_when_ocr_reads_ten_as_tu() -> None:
    extractor = PPStructureOCRHybridExtractor(overlap_threshold=0.5)
    structure_text = (
        "十一、其它约定事项：其它未尽事宜，双方协商解决。技术协议与本合同具法律效力。"
        "十二、本合同自双方签字盖章之日起生效。本合同1式4份"
    )
    structure_block = _block(
        "p2_ppstructure_b4",
        58,
        179,
        533,
        216,
        text=structure_text,
    )
    children = [
        _block(
            "p2_ppocrv5_b6",
            60,
            181,
            516,
            192,
            text="十一、其它约定事项：其它未尽事宜，双方协商解决。技术协议与本合同具法律效力。",
        ).model_copy(update={"layout_block_id": structure_block.block_id}),
        _block(
            "p2_ppocrv5_b7",
            61,
            203,
            380,
            216,
            text="土二、本合同自双方签字盖章之日起生效。本合同1式4份",
        ).model_copy(update={"layout_block_id": structure_block.block_id}),
    ]

    result = extractor._consolidate_structure_text_blocks(
        children,
        {structure_block.block_id: structure_block},
        set(),
    )

    assert len(result) == 1
    assert (
        result[0].text
        == "十一、其它约定事项:其它未尽事宜,双方协商解决。技术协议与本合同具法律效力。十二、本合同自双方签字盖章之日起生效。本合同1式4份"
    )
    assert result[0].source == "ppstructure_text"


def test_hybrid_repairs_low_confidence_short_header_annotation_from_structure_text() -> None:
    extractor = PPStructureOCRHybridExtractor(overlap_threshold=0.5)
    structure_block = _block(
        "p1_ppstructure_b2",
        356.5,
        58.5,
        525.5,
        109.0,
        block_type="header",
        text="(NN-20260522-0004永南京国电南自电网自动化有限公司",
    )
    children = [
        _block(
            "p1_ppocrv5_b2",
            354.5,
            55.0,
            519.0,
            87.0,
            block_type="header",
            text="(NM/N-20260522-00041",
        ).model_copy(update={"layout_block_id": structure_block.block_id, "confidence": 0.90}),
        _block(
            "p1_ppocrv5_b4",
            462.5,
            79.0,
            487.5,
            100.5,
            block_type="header",
            text="3",
        ).model_copy(update={"layout_block_id": structure_block.block_id, "confidence": 0.263}),
        _block(
            "p1_ppocrv5_b5",
            361.0,
            97.5,
            526.5,
            109.5,
            block_type="header",
            text="南京国电南自电网自动化有限公司",
        ).model_copy(update={"layout_block_id": structure_block.block_id, "confidence": 0.99}),
    ]

    result = extractor._repair_short_structure_annotations(
        children,
        {structure_block.block_id: structure_block},
    )

    repaired = next(block for block in result if block.block_id == "p1_ppocrv5_b4")
    assert repaired.text == "永"
    assert "ppstructure_short_annotation_repair" in repaired.source
    assert next(block for block in result if block.block_id == "p1_ppocrv5_b2").text == "(NM/N-20260522-00041"


class _StaticExtractor:
    name = "ppstructure_ocr_hybrid"

    def __init__(self, result: ExtractionResult) -> None:
        self.result = result

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        return copy.deepcopy(self.result)


def _block(
    block_id: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    block_type: str = "text",
    text: str = "text",
    flow_role: str = "",
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=1,
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        block_type=block_type,
        flow_role=flow_role,
    )
