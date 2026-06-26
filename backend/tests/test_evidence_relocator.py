from pathlib import Path

import fitz

from app.models import BBox, DiffItem, EvidenceBox
from app.services.evidence_relocator import EvidenceRelocator


def _write_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 96), text)
    doc.save(path)
    doc.close()


def _low_original_evidence() -> EvidenceBox:
    return EvidenceBox(
        page_no=1,
        bbox=BBox(x0=10, y0=10, x1=80, y1=30),
        method="block_fallback",
        text="付款金额为100元",
        highlight_type="MODIFY",
        confidence=0.46,
        evidence_quality="LOW",
    )


def test_relocator_upgrades_low_confidence_original_evidence(tmp_path: Path):
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_pdf(original_pdf, "付款金额为100元")
    _write_pdf(compare_pdf, "付款金额为120元")
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_snippet="付款金额为100元",
        compare_snippet="付款金额为120元",
        original_evidence=[_low_original_evidence()],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )

    result = EvidenceRelocator().relocate(
        diff,
        side="original",
        page_no=1,
        original_pdf=original_pdf,
        compare_pdf=compare_pdf,
    )

    assert result.status == "SUCCEEDED"
    assert result.evidence
    assert result.evidence[0].method == "text_exact"
    assert result.evidence[0].confidence > diff.original_evidence[0].confidence
    assert result.before_quality["max_confidence"] == 0.46
    assert result.after_quality["max_confidence"] > 0.46
    assert diff.original_evidence[0].method == "block_fallback"


def test_relocator_skips_existing_high_confidence_evidence(tmp_path: Path):
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_pdf(original_pdf, "付款金额为100元")
    _write_pdf(compare_pdf, "付款金额为120元")
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_snippet="付款金额为100元",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=10, x1=80, y1=30),
                method="text_exact",
                text="付款金额为100元",
                highlight_type="MODIFY",
                confidence=0.98,
                evidence_quality="HIGH",
            )
        ],
    )

    result = EvidenceRelocator().relocate(
        diff,
        side="original",
        page_no=1,
        original_pdf=original_pdf,
        compare_pdf=compare_pdf,
    )

    assert result.status == "SKIPPED"
    assert result.reason == "EXISTING_EVIDENCE_HIGH_CONFIDENCE"
    assert result.evidence == []


def test_relocator_rejects_wrong_page_candidate(tmp_path: Path):
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_pdf(original_pdf, "付款金额为100元")
    _write_pdf(compare_pdf, "付款金额为120元")
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_snippet="付款金额为100元",
        compare_snippet="付款金额为120元",
        original_evidence=[_low_original_evidence()],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )

    result = EvidenceRelocator().relocate(
        diff,
        side="original",
        page_no=2,
        original_pdf=original_pdf,
        compare_pdf=compare_pdf,
    )

    assert result.status == "FAILED"
    assert result.reason == "NO_ACCEPTED_CANDIDATE"
    assert result.evidence == []


def test_relocator_fails_without_side_text_signal(tmp_path: Path):
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_pdf(original_pdf, "付款金额为100元")
    _write_pdf(compare_pdf, "付款金额为120元")
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_evidence=[_low_original_evidence()],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )

    result = EvidenceRelocator().relocate(
        diff,
        side="original",
        page_no=1,
        original_pdf=original_pdf,
        compare_pdf=compare_pdf,
    )

    assert result.status == "FAILED"
    assert result.reason == "NO_SIDE_TEXT_SIGNAL"
    assert result.changed_evidence is False
