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
