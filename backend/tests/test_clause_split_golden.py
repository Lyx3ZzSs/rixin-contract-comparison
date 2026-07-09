from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models import BBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "clause_split_hierarchy.json"


def test_clause_splitter_matches_hierarchy_golden_fixture() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    clauses = ClauseSplitter().split(_document_from_fixture(payload["document"]), "O")

    assert [_clause_snapshot(clause) for clause in clauses] == payload["expected_clauses"]


def _document_from_fixture(payload: dict[str, Any]) -> Document:
    pages = []
    for page_payload in payload["pages"]:
        page_no = page_payload["page_no"]
        pages.append(
            Page(
                page_no=page_no,
                width=page_payload["width"],
                height=page_payload["height"],
                blocks=[
                    TextBlock(
                        block_id=block["block_id"],
                        page_no=page_no,
                        text=block["text"],
                        bbox=_bbox(block["bbox"]),
                    )
                    for block in page_payload["blocks"]
                ],
            )
        )
    return Document(
        filename=payload["filename"],
        path=payload["filename"],
        page_count=len(pages),
        pages=pages,
    )


def _bbox(values: list[float]) -> BBox:
    return BBox(x0=values[0], y0=values[1], x1=values[2], y1=values[3])


def _clause_snapshot(clause: Any) -> dict[str, Any]:
    return {
        "clause_no": clause.clause_no,
        "title": clause.title,
        "section_type": clause.section_type,
        "section_path": clause.section_path,
        "clause_key": clause.clause_key,
        "page_numbers": clause.page_numbers,
        "source_block_ids": clause.source_block_ids,
        "split_flags": clause.split_flags,
        "segmentation_reason_base": clause.segmentation_reason.split("|", maxsplit=1)[0],
        "text": clause.text,
    }
