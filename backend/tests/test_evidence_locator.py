from app.models import BBox, DiffItem, EvidenceBox
from app.services.evidence_locator import EvidenceLocator


def test_evidence_locator_keeps_signing_region_when_text_evidence_overlaps() -> None:
    signing_evidence = EvidenceBox(
        page_no=2,
        bbox=BBox(x0=37, y0=242, x1=556, y1=687),
        method="signing_region",
        text="甲方：浙江公司；住所：创业孵化基地1号楼0814室；法人代表：雍正",
        highlight_type="MODIFY",
        confidence=0.85,
        evidence_quality="HIGH",
    )
    signing_diff = DiffItem(
        diff_id="D012",
        diff_type="MODIFY",
        source_type="signing_region",
        original_evidence=[signing_evidence],
    )
    table_diff = DiffItem(
        diff_id="D006",
        diff_type="DELETE",
        source_type="table",
        original_evidence=[
            EvidenceBox(
                page_no=2,
                bbox=BBox(x0=115, y0=400, x1=260, y1=418),
                method="table_cell",
                text="创业孵化基地1号楼0814室",
                highlight_type="DELETE",
            )
        ],
    )

    result = EvidenceLocator().locate([signing_diff, table_diff])

    assert result[0].original_evidence == [signing_evidence]


def test_evidence_locator_preserves_signing_region_detector_confidence() -> None:
    evidence = EvidenceBox(
        page_no=2,
        bbox=BBox(x0=37, y0=242, x1=556, y1=687),
        method="signing_region",
        text="甲方：浙江公司",
        highlight_type="MODIFY",
        confidence=0.85,
        evidence_quality="HIGH",
    )
    diff = DiffItem(
        diff_id="D012",
        diff_type="MODIFY",
        source_type="signing_region",
        original_evidence=[evidence],
    )

    EvidenceLocator().assign_evidence_confidence([diff])

    assert diff.original_evidence[0].confidence == 0.85
    assert diff.original_evidence[0].evidence_quality == "HIGH"


def test_evidence_locator_prefers_footer_aggregate_over_overlapping_clause_text() -> None:
    footer_diff = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="header_footer",
        compare_evidence=[
            EvidenceBox(
                page_no=45,
                bbox=BBox(x0=400.5, y0=784.5, x1=468.0, y1=826.5),
                method="header_footer",
                text="奇科",
                highlight_type="ADD",
            )
        ],
    )
    clause_diff = DiffItem(
        diff_id="D103",
        diff_type="ADD",
        source_type="clause",
        compare_evidence=[
            EvidenceBox(
                page_no=45,
                bbox=BBox(x0=407.7, y0=783.7, x1=468.8, y1=827.3),
                method="char_exact",
                text="奇科",
                highlight_type="ADD",
            )
        ],
    )

    EvidenceLocator().locate([footer_diff, clause_diff])

    assert [evidence.text for evidence in footer_diff.compare_evidence] == ["奇科"]
    assert clause_diff.compare_evidence == []


def test_evidence_locator_prefers_cover_extra_over_overlapping_clause_fragment() -> None:
    cover_diff = DiffItem(
        diff_id="D024",
        diff_type="ADD",
        source_type="metadata",
        title="封面额外文本",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=364.5, y0=166.5, x1=525.5, y1=223.5),
                method="cover_extra",
                text="GN/H1-2060518-0013.",
                highlight_type="ADD",
            )
        ],
    )
    clause_diff = DiffItem(
        diff_id="D080",
        diff_type="ADD",
        source_type="clause",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=394.7, y0=185.2, x1=532.8, y1=204.8),
                method="char_exact",
                text="H1-2060518-0013.",
                highlight_type="ADD",
            )
        ],
    )

    EvidenceLocator().locate([cover_diff, clause_diff])

    assert [evidence.text for evidence in cover_diff.compare_evidence] == ["GN/H1-2060518-0013."]
    assert clause_diff.compare_evidence == []
