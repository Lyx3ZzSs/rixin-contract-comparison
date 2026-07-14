import fitz

from app.models import BBox, DiffItem, EvidenceBox
from app.services.text_coordinate_locator import TextCoordinateLocator


def test_text_coordinate_locator_treats_signing_region_as_precise_evidence() -> None:
    existing = [
        EvidenceBox(
            page_no=2,
            bbox=BBox(x0=37, y0=242, x1=556, y1=687),
            method="signing_region",
            text="甲方：浙江公司",
            highlight_type="MODIFY",
        )
    ]

    assert TextCoordinateLocator()._should_refine(existing) is False


def test_text_coordinate_locator_preserves_aggregated_header_footer_evidence(tmp_path) -> None:
    pdf_path = tmp_path / "compare.pdf"
    pdf = fitz.open()
    first_page = pdf.new_page()
    first_page.insert_text((400, 800), "Footer")
    pdf.new_page()
    pdf.save(pdf_path)
    pdf.close()

    diff = DiffItem(
        diff_id="D_footer",
        diff_type="ADD",
        source_type="header_footer",
        compare_snippet="Footer",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=400, y0=784, x1=440, y1=820),
                method="header_footer",
                text="FooterSuffix",
                highlight_type="ADD",
            ),
            EvidenceBox(
                page_no=2,
                bbox=BBox(x0=400, y0=784, x1=440, y1=820),
                method="header_footer",
                text="Variant",
                highlight_type="ADD",
            ),
        ],
    )

    TextCoordinateLocator().refine(pdf_path, pdf_path, [diff])

    assert [evidence.page_no for evidence in diff.compare_evidence] == [1, 2]
    assert [evidence.text for evidence in diff.compare_evidence] == ["FooterSuffix", "Variant"]
