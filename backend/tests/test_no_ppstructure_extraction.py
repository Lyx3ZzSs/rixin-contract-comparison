"""Keep the production extraction entry free of the removed service."""

from pathlib import Path

from app.models import BBox, TextBlock
from app.services.extractors.factory import build_compare_document_extractor
from app.services.table_compare.parser import LogicalTableParser


APP = Path(__file__).resolve().parents[1] / "app"


def test_production_source_has_no_layout_parsing_request_or_legacy_pipeline():
    forbidden = (
        "/layout-parsing",
        "PPStructureOCRHybridExtractor",
        "PPStructureExtractor",
        "PPStructureLayoutAdapter",
        "AutoDocumentExtractor",
        "ExtractorRegistry",
    )
    for source in APP.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert all(term not in text for term in forbidden), source


def test_compare_factory_builds_only_v3_extraction():
    assert type(build_compare_document_extractor()).__name__ == "ContractExtractionV3"


def test_v3_writer_cannot_emit_historical_source_tag():
    for source in (APP / "services" / "extractors" / "contract_v3").rglob("*.py"):
        assert "ppstructure" not in source.read_text(encoding="utf-8").casefold(), source


def test_new_table_provenance_is_generic_and_old_table_provenance_is_readable():
    html = "<table><tr><td>Value</td></tr></table>"
    block = TextBlock(
        block_id="table-1", page_no=1, text="Value", raw_html=html,
        bbox=BBox(x0=0, y0=0, x1=100, y1=50), block_type="table", source="native_pdf",
    )
    parser = LogicalTableParser()
    assert parser.parse_tables([(block, html)])[0].source == "table_html"
    block.source = "ppstructure"
    assert parser.parse_tables([(block, html)])[0].source == "ppstructure_html"
