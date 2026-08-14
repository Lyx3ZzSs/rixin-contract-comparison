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


def test_text_coordinate_locator_rejects_partial_cjk_search_result() -> None:
    locator = TextCoordinateLocator()

    assert locator._plausible_text_extent("委托代理人：", fitz.Rect(0, 0, 21, 12)) is False
    assert locator._plausible_text_extent("法人代表或授权委托人：", fitz.Rect(0, 0, 116, 12)) is True


def test_text_coordinate_locator_matches_spaced_words_on_one_line(tmp_path) -> None:
    pdf_path = tmp_path / "spaced-label.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((60, 100), "A")
    page.insert_text((80, 100), "B")
    page.insert_text((100, 100), "C")
    pdf.save(pdf_path)
    pdf.close()

    pdf = fitz.open(pdf_path)
    candidates = TextCoordinateLocator()._word_sequence_candidates(pdf[0], "ABC", 1, "DELETE")
    pdf.close()

    assert len(candidates) == 1
    assert candidates[0].bbox.x0 < 61
    assert candidates[0].bbox.x1 > 107


def test_text_coordinate_locator_normalizes_fullwidth_punctuation() -> None:
    assert TextCoordinateLocator()._compact_text("（签 字）") == "(签字)"


def test_text_coordinate_locator_matches_one_normalized_word(tmp_path) -> None:
    pdf_path = tmp_path / "one-word.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((60, 100), "(Signed)")
    pdf.save(pdf_path)
    pdf.close()

    pdf = fitz.open(pdf_path)
    candidates = TextCoordinateLocator()._word_sequence_candidates(pdf[0], "(Signed)", 1, "DELETE")
    pdf.close()

    assert len(candidates) == 1


def test_text_coordinate_locator_refines_multiline_segments_independently(tmp_path) -> None:
    pdf_path = tmp_path / "multiline.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((60, 100), "Address: first line")
    page.insert_text((60, 130), "second line")
    pdf.save(pdf_path)
    pdf.close()
    existing = [
        EvidenceBox(
            page_no=1,
            bbox=BBox(x0=50, y0=70, x1=250, y1=115),
            method="signing_region_element",
            text="Address: first line",
            highlight_type="DELETE",
        ),
        EvidenceBox(
            page_no=1,
            bbox=BBox(x0=50, y0=115, x1=250, y1=150),
            method="signing_region_element",
            text="second line",
            highlight_type="DELETE",
        ),
    ]

    pdf = fitz.open(pdf_path)
    refined = TextCoordinateLocator()._locate_snippet(
        pdf,
        "Address: first linesecond line",
        existing,
        "DELETE",
    )
    pdf.close()

    assert len(refined) == 2
    assert all(evidence.method == "text_exact" for evidence in refined)
    assert all(evidence.bbox.y1 - evidence.bbox.y0 < 20 for evidence in refined)


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
