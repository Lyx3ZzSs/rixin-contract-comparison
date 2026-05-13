from __future__ import annotations

from app.models import BBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.matcher import ClauseMatcher
from app.services.normalizer import TextNormalizer


def test_text_normalizer_removes_page_number_and_compacts_text() -> None:
    text = " 合同编号：ABC-1 \n 第 1 页 \n付款　期限 为  30 天。\n\n\n"
    normalized = TextNormalizer().normalize(text)
    assert "第 1 页" not in normalized
    assert "付款 期限 为 30 天。" in normalized


def test_clause_splitter_detects_numbered_clauses() -> None:
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
                        block_id="p1_b1",
                        page_no=1,
                        text="1. Payment\nBuyer shall pay within 30 days.\n2. Delivery\nSeller shall deliver goods.",
                        bbox=BBox(x0=10, y0=10, x1=500, y1=120),
                    )
                ],
            )
        ],
    )
    clauses = ClauseSplitter().split(document, "O")
    assert len(clauses) == 2
    assert clauses[0].clause_no == "1"
    assert "Buyer shall pay" in clauses[0].text


def test_clause_matcher_matches_by_clause_number() -> None:
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
                        block_id="p1_b1",
                        page_no=1,
                        text="1. Payment\nBuyer shall pay within 30 days.",
                        bbox=BBox(x0=10, y0=10, x1=500, y1=80),
                    )
                ],
            )
        ],
    )
    splitter = ClauseSplitter()
    left = splitter.split(document, "O")
    right = splitter.split(document, "N")
    pairs = ClauseMatcher().match(left, right)
    assert pairs[0].match_method == "clause_no"
    assert pairs[0].score == 100

