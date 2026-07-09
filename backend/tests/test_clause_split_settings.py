from __future__ import annotations

from app.models import BBox, Document, Page, TextBlock
from app.services.clause_split_settings import DEFAULT_CLAUSE_SPLIT_SETTINGS, ClauseSplitSettings
from app.services.clause_splitter import ClauseSplitter


def test_clause_splitter_accepts_code_level_split_settings() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="b1",
                        page_no=1,
                        text="第一条 付款\n甲方应在收到发票后付款。",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=130),
                        block_type="custom_skip",
                    )
                ],
            )
        ],
    )
    settings = ClauseSplitSettings(
        skip_block_types=DEFAULT_CLAUSE_SPLIT_SETTINGS.skip_block_types | {"custom_skip"},
    )

    assert ClauseSplitter().split(document, "O")
    assert ClauseSplitter(split_settings=settings).split(document, "O") == []


def test_default_clause_split_settings_are_shared_as_read_only_defaults() -> None:
    assert "footer" in DEFAULT_CLAUSE_SPLIT_SETTINGS.skip_block_types
    assert DEFAULT_CLAUSE_SPLIT_SETTINGS.section_role_map["appendix"] == "appendix"
