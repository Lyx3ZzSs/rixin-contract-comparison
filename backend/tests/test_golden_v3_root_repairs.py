from pathlib import Path
import fitz
import pytest
from app.models import BBox, DiffItem, Document, EvidenceBox, Page, TextBlock, TextSourceEvidence
from app.services.cover.date_ocr import CoverDateOCR, date_precision
from app.services.cover.facade import CoverMetadataComparator
from app.services.header_footer_compare import HeaderFooterComparator
from app.services.signing_region.visual_recall import WholePageSigningVisualRecall, SigningVisualRecallResult
from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType as Kind
from app.services.seal_visual_equivalence import corresponding_red_graphics


def doc(path="missing.pdf", blocks=()):
    return Document(
        filename=Path(path).name,
        path=str(path),
        page_count=1,
        pages=[Page(page_no=1, width=600, height=800, blocks=list(blocks))],
    )


def block(text, bbox=None, **kwargs):
    return TextBlock(block_id="b1", page_no=1, text=text, bbox=bbox or BBox(x0=250, y0=650, x1=360, y1=690), **kwargs)


def pdf(tmp_path, name, draw):
    path = tmp_path / name
    with fitz.open() as file:
        page = file.new_page(width=600, height=800)
        draw(page)
        file.save(path)
    return path


def test_adjacent_complete_round_and_name_seals_survive_reassembly(tmp_path):
    def draw(page):
        page.draw_circle((220, 180), 55, color=(1, 0, 0), width=3)
        page.draw_rect(fitz.Rect(270, 222, 320, 272), color=(1, 0, 0), width=3)

    objects = WholePageSigningVisualRecall().detect(doc(pdf(tmp_path, "adjacent.pdf", draw)))
    assert len([x for x in objects if x.object_type == Kind.COMPANY_ROUND_SEAL]) == 1
    assert len([x for x in objects if x.object_type == Kind.PERSONAL_NAME_SEAL]) == 1


def test_broken_round_seal_still_reassembles(tmp_path):
    def draw(page):
        page.draw_circle((220, 180), 55, color=(1, 0, 0), width=3)
        page.draw_rect(fitz.Rect(217, 115, 223, 245), color=(1, 1, 1), fill=(1, 1, 1))

    objects = WholePageSigningVisualRecall().detect(doc(pdf(tmp_path, "broken.pdf", draw)))
    assert len(objects) == 1
    assert objects[0].object_type == Kind.COMPANY_ROUND_SEAL
    assert objects[0].raw_ref["reason"] == "FRAGMENTED_RED_SEAL_REASSEMBLED"


def test_inset_edge_is_recalled_only_on_aligned_counterpart_page(tmp_path):
    a = doc(pdf(tmp_path, "inset.pdf", lambda p: p.draw_rect(fitz.Rect(524, 160, 534, 220), color=(1, 0, 0), width=2)))
    b = doc(pdf(tmp_path, "edge.pdf", lambda p: p.draw_rect(fitz.Rect(568, 135, 580, 200), color=(1, 0, 0), width=2)))
    recall = WholePageSigningVisualRecall()
    assert not recall.detect(a)
    result = recall.compare(a, b)
    assert not result.additions
    assert result.matched_pairs[0][0].raw_ref["counterpart_guided_recall"]
    blank = doc(pdf(tmp_path, "blank.pdf", lambda p: None))
    assert recall.compare(blank, b).additions


def test_source_conflict_is_content_review_even_in_explicit_header():
    bbox = BBox(x0=250, y0=20, x1=470, y1=70)
    b = block("AX-20260101", bbox, block_type="header", confidence=0.84, source="ppocrv5_layout_matched")
    b.source_evidence = [
        TextSourceEvidence(source=s, raw_text=t, page_no=1, bbox=bbox, confidence=0.9)
        for s, t in [("ppocrv5", "AX-20260101"), ("ppstructure", "AY-20260101")]
    ]
    diff = HeaderFooterComparator().build_diffs(doc(), doc(blocks=[b]))[0]
    assert diff.content_recognition_status == "NEEDS_REVIEW"
    assert {x["text"] for x in diff.content_candidates} == {"AX-20260101", "AY-20260101"}
    assert diff.compare_evidence[0].text_confidence == 0.84
    b.source_evidence[1].raw_text = b.text
    assert HeaderFooterComparator().build_diffs(doc(), doc(blocks=[b]))[0].content_recognition_status == "CONFIRMED"


@pytest.mark.parametrize(
    "text,precision",
    [
        ("2026年7月", "month"),
        ("2024年2月29日", "day"),
        ("2026年2月29日", None),
        ("226年7月", None),
        ("2026年13月", None),
    ],
)
def test_calendar_precision_and_invalid_dates(text, precision):
    assert date_precision(text) == precision


class DateReader:
    def __init__(self, values):
        self.values = iter(values)

    def predict(self, *args, **kwargs):
        return next(self.values)

    def payload_to_document(self, value, path):
        return doc(path, [block(value, confidence=0.95)])


@pytest.mark.parametrize(
    "values,recovered",
    [(["226年7月", "2026年7月", "2026年7月"], True), (["2026年7月", "2028年7月", "2026年7月"], False)],
)
def test_cover_date_requires_two_local_readings_and_preserves_unresolved(tmp_path, values, recovered):
    path = pdf(tmp_path, "date.pdf", lambda p: p.insert_text((260, 670), "2026 / 7"))
    b = block("226年7月", confidence=0.9999)
    document = doc(path, [b])
    diagnostics = CoverDateOCR(DateReader(values)).enrich(document)
    assert (diagnostics[0]["status"] == "RECOVERED") == recovered
    assert b.text == ("2026年7月" if recovered else "226年7月")
    assert len(diagnostics[0]["readings"]) == 3
    assert all(r["input_sha256"] for r in diagnostics[0]["readings"])
    title = block("项目名称：示例合同", BBox(x0=100, y0=100, x1=400, y1=130))
    title.block_id = "title"
    document.pages[0].blocks.insert(0, title)
    diffs = CoverMetadataComparator().build_diffs(doc(), document)
    date_diff = next(d for d in diffs if "年7月" in d.compare_text)
    assert date_diff.content_recognition_status == ("CONFIRMED" if recovered else "NEEDS_REVIEW")
    if recovered:
        assert date_diff.title == "封面额外文本"
        assert date_diff.match_score_details["field_key"] == "cover_date"
        assert date_diff.match_score_details["compare_date_precision"] == "month"


def obj(kind=Kind.EDGE_SEAM_SEAL):
    return SigningVisualObject(
        object_id="x",
        object_type=kind,
        page_no=1,
        bbox=BBox(x0=550, y0=120, x1=590, y1=220),
        visual_detection_confidence=0.9,
    )


def test_matched_edge_owns_vertical_ocr_but_not_readable_change_or_header():
    a, b = obj(), obj()
    result = SigningVisualRecallResult(matched_pairs=[(a, b)])
    evidence = EvidenceBox(page_no=1, bbox=a.bbox, text="garbled")
    noise = DiffItem(diff_id="noise", diff_type="DELETE", source_type="header_footer", original_evidence=[evidence])
    header = noise.model_copy(deep=True)
    header.diff_id = "header"
    header.original_evidence[0].bbox = BBox(x0=100, y0=10, x1=450, y1=30)
    changed = DiffItem(
        diff_id="changed",
        diff_type="MODIFY",
        source_type="seal",
        original_text="甲公司",
        compare_text="乙公司",
        original_evidence=[evidence],
        compare_evidence=[evidence],
    )
    kept = WholePageSigningVisualRecall().reconcile_matched_edge_diffs(result, [noise, header, changed], doc(), doc())
    assert [d.diff_id for d in kept] == ["header", "changed"]


def test_edge_counterevidence_is_used_by_new_diff():
    candidate = obj()
    candidate.raw_ref["counterpart_edge_present"] = True
    diff = WholePageSigningVisualRecall._new_diff(candidate, 1)
    assert "新增与否待复核" in diff.readable_change
    assert diff.quality_status == "NEEDS_REVIEW"


def test_red_graphic_equivalence_rejects_different_marks(tmp_path):
    import random

    def marks(seed):
        def draw(page):
            rng = random.Random(seed)
            for i in range(70):
                x, y = rng.uniform(551, 587), rng.uniform(123, 216)
                page.draw_line((x, y), (x + rng.uniform(-3, 3), y + rng.uniform(-3, 3)), color=(1, 0, 0), width=1)

        return draw

    a = doc(pdf(tmp_path, "mark-a.pdf", marks(2)))
    same = doc(pdf(tmp_path, "mark-same.pdf", marks(2)))
    different = doc(pdf(tmp_path, "mark-b.pdf", marks(8)))
    assert corresponding_red_graphics(a, obj(), same, obj())["equivalent"]
    assert not corresponding_red_graphics(a, obj(), different, obj())["equivalent"]


def test_paragraph_layout_reading_is_not_a_conflicting_line_reading():
    bbox = BBox(x0=250, y0=20, x1=470, y1=45)
    b = block("AX-20260101", bbox, block_type="header", confidence=0.9)
    b.source_evidence = [
        TextSourceEvidence(source="ppocrv5", raw_text=b.text, page_no=1, bbox=bbox, confidence=0.9),
        TextSourceEvidence(
            source="ppstructure",
            raw_text="AX-20260101附件说明",
            page_no=1,
            bbox=BBox(x0=250, y0=20, x1=470, y1=90),
            confidence=1,
        ),
    ]
    diff = HeaderFooterComparator().build_diffs(doc(), doc(blocks=[b]))[0]
    assert diff.content_recognition_status == "CONFIRMED"
    assert len(diff.match_score_details["recognition_evidence"][0]["readings"]) == 2


def test_inset_layout_seal_is_not_globally_retyped_as_seam(tmp_path):
    path = pdf(
        tmp_path, "inset-seal.pdf", lambda p: p.draw_rect(fitz.Rect(520, 160, 540, 225), color=(1, 0, 0), width=2)
    )
    region = block("", BBox(x0=519, y0=159, x1=541, y1=226), block_type="seal")
    assert not WholePageSigningVisualRecall().detect(doc(path, [region]))


def test_repeated_short_footer_consensus_survives_one_noisy_page():
    from app.services.recognition_evidence import retain_recognition_evidence

    pages, evidences = [], []
    for page_no in range(1, 6):
        b = block("黄科", BBox(x0=400, y0=750, x1=450, y1=785))
        b.page_no = page_no
        b.block_id = str(page_no)
        b.source_evidence = [
            TextSourceEvidence(source="ppocrv5", raw_text="黄科", page_no=page_no, bbox=b.bbox, confidence=0.98),
            TextSourceEvidence(
                source="ppstructure",
                raw_text="科" if page_no == 1 else "黄科",
                page_no=page_no,
                bbox=b.bbox,
                confidence=0.99,
            ),
        ]
        pages.append(Page(page_no=page_no, width=600, height=800, blocks=[b]))
        evidences.append(EvidenceBox(page_no=page_no, bbox=b.bbox, text=b.text))
    document = Document(filename="footer.pdf", path="footer.pdf", page_count=5, pages=pages)
    diff = DiffItem(
        diff_id="footer",
        diff_type="ADD",
        title="页脚",
        source_type="header_footer",
        compare_text="黄科",
        compare_evidence=evidences,
    )
    retain_recognition_evidence([diff], doc(), document)
    assert diff.content_recognition_status == "CONFIRMED"
    assert diff.match_score_details["repeated_footer_consensus"]["compare"]["agreeing_pages"] == 4
    # The same conflict on a unique page has no document-level consensus.
    single = DiffItem(
        diff_id="single",
        diff_type="ADD",
        title="页脚",
        source_type="header_footer",
        compare_text="黄科",
        compare_evidence=evidences[:1],
    )
    retain_recognition_evidence([single], doc(), document)
    assert single.content_recognition_status == "NEEDS_REVIEW"
