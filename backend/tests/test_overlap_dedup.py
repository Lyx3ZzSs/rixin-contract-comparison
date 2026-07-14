from app.models import BBox, DiffItem, EvidenceBox, TextRange
from app.services.diff.overlap_dedup import deduplicate_overlaps


def _evidence(text: str, highlight_type: str) -> EvidenceBox:
    return EvidenceBox(
        page_no=7,
        bbox=BBox(x0=40, y0=300, x1=540, y1=560),
        method="signing_region",
        text=text,
        highlight_type=highlight_type,
        confidence=0.9,
        evidence_quality="HIGH",
    )


def test_overlap_dedup_preserves_signing_region_modify_evidence() -> None:
    original_text = "甲方：【南京国电南自电网自动化有限公司】\n乙方：【国能日新科技股份有限公司】（盖章）"
    compare_text = "甲方：【南京国电南自电网自动化有限公司】（盖章）\n乙方：【国能日新科技股份有限公司】（盖章）"
    signing_diff = DiffItem(
        diff_id="D007",
        diff_type="MODIFY",
        source_type="signing_region",
        title="签署区（第7页）",
        original_text=original_text,
        compare_text=compare_text,
        original_snippet=original_text,
        compare_snippet=compare_text,
        original_change_ranges=[TextRange(start=0, end=len(original_text), highlight_type="MODIFY")],
        compare_change_ranges=[TextRange(start=0, end=len(compare_text), highlight_type="MODIFY")],
        original_evidence=[_evidence(original_text, "MODIFY")],
        compare_evidence=[_evidence(compare_text, "MODIFY")],
    )
    overlapping_add = DiffItem(
        diff_id="D010",
        diff_type="ADD",
        source_type="clause",
        title="国能日新科技股份有限公司",
        compare_text=original_text,
        compare_snippet=original_text,
        compare_evidence=[
            EvidenceBox(
                page_no=8,
                bbox=BBox(x0=60, y0=70, x1=360, y1=90),
                method="char_exact",
                text=original_text,
                highlight_type="ADD",
            )
        ],
    )

    result = deduplicate_overlaps([signing_diff, overlapping_add])

    deduped_signing = next(diff for diff in result if diff.diff_id == "D007")
    assert deduped_signing.original_snippet == original_text
    assert deduped_signing.original_evidence == signing_diff.original_evidence
