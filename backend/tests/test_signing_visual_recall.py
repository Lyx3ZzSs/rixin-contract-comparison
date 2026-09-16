from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType
from app.services.signing_region.visual_recall import SigningVisualRecallResult, WholePageSigningVisualRecall
from app.services.diff_quality import DiffQualityProcessor
from app.services.pipeline_stages import _reconcile_visual_signing_date_diffs


def _write_pdf(tmp_path: Path, name: str, draw) -> Path:
    path = tmp_path / name
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    draw(page)
    pdf.save(path)
    pdf.close()
    return path


def _document(path: Path, blocks: list[TextBlock]) -> Document:
    return Document(
        filename=path.name,
        path=str(path),
        page_count=1,
        pages=[Page(page_no=1, width=600, height=800, blocks=blocks)],
    )


def _block(block_id: str, text: str, bbox: BBox, *, block_type: str = "text") -> TextBlock:
    return TextBlock(block_id=block_id, page_no=1, text=text, bbox=bbox, block_type=block_type)


def _object(
    object_type: SigningVisualObjectType,
    *,
    page_no: int = 1,
    bbox: BBox | None = None,
    confidence: float = 0.9,
) -> SigningVisualObject:
    bbox = bbox or BBox(x0=100, y0=100, x1=180, y1=180)
    return SigningVisualObject(
        object_id=f"object-{object_type.value}-{page_no}",
        object_type=object_type,
        page_no=page_no,
        bbox=bbox,
        visual_detection_confidence=confidence,
        party_role="甲方",
        party_attribution_confidence=0.9,
        supporting_pages=[page_no],
    )


def test_whole_page_recall_finds_signature_without_selected_signing_region(tmp_path: Path) -> None:
    def draw(page: fitz.Page) -> None:
        shape = page.new_shape()
        shape.draw_polyline([(185, 225), (205, 265), (225, 220), (245, 270), (265, 230)])
        shape.finish(color=(0, 0, 0), width=4)
        shape.commit()

    path = _write_pdf(tmp_path, "signature.pdf", draw)
    document = _document(
        path,
        [_block("representative", "甲方：法定代表人（签字）：", BBox(x0=80, y0=180, x1=180, y1=215))],
    )

    objects = WholePageSigningVisualRecall().detect(document)

    signatures = [item for item in objects if item.object_type == SigningVisualObjectType.HANDWRITTEN_SIGNATURE]
    assert len(signatures) == 1
    assert signatures[0].party_role == "甲方"


def test_party_attribution_supports_stacked_parties() -> None:
    page = Page(
        page_no=1,
        width=600,
        height=800,
        blocks=[
            _block("party-a", "甲方（盖章）：A公司", BBox(x0=80, y0=80, x1=320, y1=110)),
            _block("party-b", "乙方（盖章）：B公司", BBox(x0=80, y0=430, x1=320, y1=460)),
        ],
    )

    party, reason, confidence = WholePageSigningVisualRecall._attribute_party(
        page,
        BBox(x0=360, y0=130, x1=470, y1=240),
        SigningVisualObjectType.COMPANY_ROUND_SEAL,
    )

    assert party == "甲方"
    assert reason == "NEAREST_PARTY_ANCHOR"
    assert confidence > 0.6


def test_company_round_seal_and_personal_name_seal_are_separate_objects(tmp_path: Path) -> None:
    def draw(page: fitz.Page) -> None:
        shape = page.new_shape()
        shape.draw_circle((165, 210), 62)
        shape.draw_rect(fitz.Rect(250, 185, 305, 240))
        shape.finish(color=(1, 0, 0), width=4)
        shape.commit()

    path = _write_pdf(tmp_path, "two-seals.pdf", draw)
    document = _document(
        path,
        [_block("party-a", "甲方（盖章）：A公司", BBox(x0=70, y0=90, x1=280, y1=120))],
    )

    objects = WholePageSigningVisualRecall().detect(document)

    company = [item for item in objects if item.object_type == SigningVisualObjectType.COMPANY_ROUND_SEAL]
    personal = [item for item in objects if item.object_type == SigningVisualObjectType.PERSONAL_NAME_SEAL]
    assert len(company) == 1
    assert len(personal) == 1
    assert WholePageSigningVisualRecall._bbox_overlap_ratio(company[0].bbox, personal[0].bbox) < 0.2


def test_personal_seal_does_not_remain_both_seal_and_signature_diffs() -> None:
    bbox = BBox(x0=250, y0=180, x1=315, y1=245)
    seal_diff = DiffItem(
        diff_id="D009",
        diff_type="ADD",
        source_type="signing_region",
        section_type="signature:seal",
        compare_text="seal",
        compare_evidence=[EvidenceBox(page_no=1, bbox=bbox, highlight_type="ADD")],
    )
    signature_diff = DiffItem(
        diff_id="D010",
        diff_type="ADD",
        source_type="signing_region",
        section_type="signature:signature",
        compare_text="已签",
        compare_evidence=[EvidenceBox(page_no=1, bbox=bbox, highlight_type="ADD")],
    )
    result = SigningVisualRecallResult(
        additions=[_object(SigningVisualObjectType.PERSONAL_NAME_SEAL, bbox=bbox)]
    )

    _, signing = WholePageSigningVisualRecall().reconcile_diffs(
        result,
        [],
        [seal_diff, signature_diff],
        start_index=11,
    )

    assert [item.diff_id for item in signing] == ["D010"]
    assert signing[0].match_score_details["object_type"] == "PERSONAL_NAME_SEAL"
    assert signing[0].section_type == "signature:signature"


def test_existing_review_diff_gets_visual_diagnostics_without_confirmation_upgrade() -> None:
    existing = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="seal",
        title="印章区域（第1页）",
        compare_text="章文 OCR",
        quality_status="NEEDS_REVIEW",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=90, y0=90, x1=190, y1=190),
                highlight_type="ADD",
            )
        ],
    )
    visual = _object(SigningVisualObjectType.COMPANY_ROUND_SEAL)

    seals, _ = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=[visual]),
        [existing],
        [],
        start_index=2,
    )

    assert seals[0].title == "甲方 · 公司圆章"
    assert seals[0].compare_text == "公司圆章（章文识别待复核）"
    assert seals[0].match_score_details["visual_replaced_text"] == "章文 OCR"
    assert seals[0].quality_status == "NEEDS_REVIEW"
    assert "VISUAL_OBJECT_EXISTENCE_CONFIRMED" not in seals[0].review_flags
    assert seals[0].match_score_details["object_type"] == "COMPANY_ROUND_SEAL"


def test_small_visual_fragment_does_not_shrink_existing_seal_bbox() -> None:
    existing_bbox = BBox(x0=80, y0=110, x1=240, y1=300)
    existing = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="seal",
        compare_evidence=[EvidenceBox(page_no=1, bbox=existing_bbox, highlight_type="ADD")],
    )
    fragment = _object(
        SigningVisualObjectType.OTHER_STAMP,
        bbox=BBox(x0=120, y0=120, x1=165, y1=145),
    )

    seals, _ = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=[fragment]),
        [existing],
        [],
        start_index=2,
    )

    assert seals[0].compare_evidence[0].bbox == existing_bbox


def test_company_seal_does_not_retype_overlapping_signing_date() -> None:
    date_bbox = BBox(x0=140, y0=580, x1=210, y1=600)
    date_diff = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="signing_region",
        title="甲方 · 签订日期",
        compare_text="2026.7.28",
        match_score_details={"field_key": "date", "change_decision": "NEEDS_REVIEW"},
        compare_evidence=[EvidenceBox(page_no=1, bbox=date_bbox, highlight_type="ADD")],
    )
    seal = _object(
        SigningVisualObjectType.COMPANY_ROUND_SEAL,
        bbox=BBox(x0=110, y0=545, x1=225, y1=645),
    )

    _, signing = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=[seal]),
        [],
        [date_diff],
        start_index=2,
    )

    assert date_diff.match_score_details.get("object_type") is None
    assert date_diff.compare_evidence[0].bbox == date_bbox
    assert any(item.match_score_details.get("object_type") == "COMPANY_ROUND_SEAL" for item in signing)


def test_visual_existence_confirmation_is_not_downgraded_by_ocr_risk() -> None:
    visual_object = _object(SigningVisualObjectType.COMPANY_ROUND_SEAL)
    diff = WholePageSigningVisualRecall._new_diff(visual_object, 1)
    diff.review_flags.append("PAGE_UNRELIABLE")
    diff.quality_status = "NEEDS_REVIEW"

    result = DiffQualityProcessor().process([diff])

    assert result.diffs[0].quality_status == "NORMAL"
    assert result.diffs[0].match_score_details["change_decision"] == "CONFIRMED"


def test_low_confidence_whole_page_candidate_is_not_upgraded_by_quality_admission() -> None:
    candidate = WholePageSigningVisualRecall._new_diff(
        _object(SigningVisualObjectType.COMPANY_ROUND_SEAL, confidence=0.78),
        1,
    )

    result = DiffQualityProcessor().process([candidate])

    assert result.diffs[0].quality_status == "NEEDS_REVIEW"
    assert result.diffs[0].match_score_details["change_decision"] == "NEEDS_REVIEW"


def test_unscoped_small_red_mark_is_diagnostic_only() -> None:
    visual_object = _object(SigningVisualObjectType.OTHER_STAMP, confidence=0.7)
    visual_object.raw_ref = {"reason": "UNSCOPED_SMALL_RED_MARK"}

    _, signing = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=[visual_object]),
        [],
        [],
        start_index=1,
    )

    assert signing == []


def test_overlapping_signature_does_not_overwrite_company_seal() -> None:
    company = _object(
        SigningVisualObjectType.COMPANY_ROUND_SEAL,
        bbox=BBox(x0=100, y0=100, x1=220, y1=220),
    )
    signature = _object(
        SigningVisualObjectType.HANDWRITTEN_SIGNATURE,
        bbox=BBox(x0=195, y0=180, x1=230, y1=225),
    )
    existing = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="seal",
        compare_evidence=[EvidenceBox(page_no=1, bbox=company.bbox, highlight_type="ADD")],
    )

    seals, signing = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=[company, signature]),
        [existing],
        [],
        start_index=2,
    )

    assert seals[0].match_score_details["object_type"] == "COMPANY_ROUND_SEAL"
    assert signing[0].match_score_details["object_type"] == "HANDWRITTEN_SIGNATURE"
    assert signing[0].diff_id.startswith("WHOLE-PAGE-")


def test_visual_handwritten_date_enriches_existing_metadata_projection() -> None:
    visual = WholePageSigningVisualRecall._new_diff(
        _object(SigningVisualObjectType.HANDWRITTEN_DATE),
        1,
    )
    visual.compare_text = "2026.6.11"
    visual.compare_snippet = "2026.6.11"
    metadata = DiffItem(
        diff_id="D010",
        diff_type="ADD",
        source_type="metadata",
        title="签署日期",
        compare_text="签字日期：2026年6月11日",
        compare_evidence=[EvidenceBox(page_no=1, bbox=visual.compare_evidence[0].bbox, highlight_type="ADD")],
    )

    diffs, remap = _reconcile_visual_signing_date_diffs([visual, metadata])

    assert [item.diff_id for item in diffs] == [metadata.diff_id]
    assert diffs[0].match_score_details["object_type"] == "HANDWRITTEN_DATE"
    assert remap == {visual.diff_id: "D010"}


def test_right_edge_seam_seal_has_no_forced_party() -> None:
    page = Page(
        page_no=1,
        width=600,
        height=800,
        blocks=[_block("party-b", "乙方（盖章）：B公司", BBox(x0=330, y0=100, x1=540, y1=130))],
    )

    party, reason, confidence = WholePageSigningVisualRecall._attribute_party(
        page,
        BBox(x0=575, y0=300, x1=600, y1=410),
        SigningVisualObjectType.EDGE_SEAM_SEAL,
    )

    assert party == "unknown"
    assert reason == "PAGE_EDGE_LOGICAL_OBJECT"
    assert confidence > 0.9


def test_multi_page_seam_fragments_aggregate_to_one_event() -> None:
    fragments = [
        _object(
            SigningVisualObjectType.EDGE_SEAM_SEAL,
            page_no=page_no,
            bbox=BBox(x0=575, y0=200 + page_no, x1=600, y1=300 + page_no),
        )
        for page_no in (1, 7, 31)
    ]

    aggregated = WholePageSigningVisualRecall._aggregate_edge_fragments(fragments)

    assert len(aggregated) == 1
    assert aggregated[0].supporting_pages == [1, 7, 31]
    assert len(aggregated[0].fragment_bboxes) == 3


def test_dense_full_document_edge_marks_remain_review_candidates() -> None:
    seam = _object(SigningVisualObjectType.EDGE_SEAM_SEAL)
    seam.supporting_pages = [1, 2, 3, 4, 5]
    seam.fragment_bboxes = [seam.bbox] * 5
    seam.raw_ref = {
        "reason": "MULTI_PAGE_EDGE_FRAGMENT_AGGREGATION",
        "document_page_count": 5,
    }

    _, diffs = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=[seam]),
        [],
        [],
        start_index=1,
    )

    assert diffs[0].quality_status == "NEEDS_REVIEW"
    assert diffs[0].match_score_details["change_decision"] == "NEEDS_REVIEW"
    assert diffs[0].match_score_details["page_coverage_ratio"] == 1.0


def test_edge_seam_event_replaces_overlapping_page_fragment_diffs() -> None:
    fragments = [
        EvidenceBox(
            page_no=page_no,
            bbox=BBox(x0=575, y0=200, x1=600, y1=300),
            highlight_type="ADD",
        )
        for page_no in (1, 3)
    ]
    existing = [
        DiffItem(
            diff_id=f"D00{index}",
            diff_type="ADD",
            source_type="seal",
            compare_evidence=[evidence],
        )
        for index, evidence in enumerate(fragments, start=1)
    ]
    seam = _object(SigningVisualObjectType.EDGE_SEAM_SEAL, bbox=fragments[0].bbox)
    seam.supporting_pages = [1, 3]
    seam.fragment_bboxes = [item.bbox for item in fragments]

    seals, _ = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=[seam]),
        existing,
        [],
        start_index=3,
    )

    assert len(seals) == 1
    assert {e.page_no for e in seals[0].compare_evidence} == {1, 3}
    assert {item.match_score_details["object_type"] for item in seals} == {"EDGE_SEAM_SEAL"}
    assert all(item.match_score_details["supporting_pages"] == [1, 3] for item in seals)


def test_edge_seam_does_not_retype_distant_signature_on_same_page() -> None:
    signature_bbox = BBox(x0=490, y0=340, x1=570, y1=385)
    signature = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="signing_region",
        title="乙方 · 授权代表签字",
        compare_evidence=[EvidenceBox(page_no=1, bbox=signature_bbox, highlight_type="ADD")],
    )
    seam = _object(
        SigningVisualObjectType.EDGE_SEAM_SEAL,
        bbox=BBox(x0=585, y0=220, x1=600, y1=270),
    )
    seam.supporting_pages = [1, 2, 3]
    seam.fragment_bboxes = [seam.bbox] * 3

    _, signing = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=[seam]),
        [],
        [signature],
        start_index=2,
    )

    assert signature.match_score_details.get("object_type") is None
    assert any(item.match_score_details.get("object_type") == "EDGE_SEAM_SEAL" for item in signing)


def test_printed_red_logo_is_not_admitted_as_seal(tmp_path: Path) -> None:
    def draw(page: fitz.Page) -> None:
        shape = page.new_shape()
        shape.draw_circle((120, 100), 45)
        shape.finish(color=(1, 0, 0), width=4)
        shape.commit()

    path = _write_pdf(tmp_path, "logo.pdf", draw)
    document = _document(
        path,
        [_block("logo", "LOGO", BBox(x0=70, y0=50, x1=170, y1=150), block_type="image")],
    )

    assert WholePageSigningVisualRecall().detect(document) == []


def test_faint_rectangular_stamp_enters_review_recall(tmp_path: Path) -> None:
    def draw(page: fitz.Page) -> None:
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(220, 150, 300, 185))
        shape.draw_line((235, 160), (285, 175))
        shape.finish(color=(0.96, 0.86, 0.88), width=4)
        shape.commit()

    path = _write_pdf(tmp_path, "faint-stamp.pdf", draw)
    document = _document(
        path,
        [_block("attachment", "公章后生效。九、附件", BBox(x0=80, y0=90, x1=220, y1=120))],
    )

    objects = WholePageSigningVisualRecall().detect(document)

    stamps = [item for item in objects if item.object_type == SigningVisualObjectType.OTHER_STAMP]
    assert len(stamps) == 1
    result = SigningVisualRecallResult(additions=stamps)
    _, diffs = WholePageSigningVisualRecall().reconcile_diffs(result, [], [], start_index=1)
    assert diffs[0].quality_status == "NEEDS_REVIEW"
    assert diffs[0].match_score_details["change_decision"] == "NEEDS_REVIEW"


def test_seal_text_uncertainty_does_not_downgrade_visual_existence() -> None:
    result = SigningVisualRecallResult(additions=[_object(SigningVisualObjectType.COMPANY_ROUND_SEAL)])

    _, diffs = WholePageSigningVisualRecall().reconcile_diffs(result, [], [], start_index=1)

    assert diffs[0].match_score_details["change_decision"] == "CONFIRMED"
    assert diffs[0].content_recognition_status == "NEEDS_REVIEW"
    assert "VISUAL_OBJECT_EXISTENCE_CONFIRMED" in diffs[0].review_flags


def test_printed_date_without_signing_context_is_not_handwritten_date(tmp_path: Path) -> None:
    def draw(page: fitz.Page) -> None:
        page.insert_text((100, 120), "2026.6.11", fontsize=14)

    path = _write_pdf(tmp_path, "printed-date.pdf", draw)
    document = _document(path, [_block("date", "2026.6.11", BBox(x0=95, y0=100, x1=180, y1=125))])

    assert not [
        item
        for item in WholePageSigningVisualRecall().detect(document)
        if item.object_type == SigningVisualObjectType.HANDWRITTEN_DATE
    ]


def test_date_candidate_with_signing_context_does_not_auto_confirm_handwriting(tmp_path: Path) -> None:
    def draw(page: fitz.Page) -> None:
        page.insert_text((100, 120), "2026.6.11", fontsize=14)

    path = _write_pdf(tmp_path, "printed-signing-date.pdf", draw)
    document = _document(
        path,
        [
            _block("party-a", "甲方（盖章）：A公司", BBox(x0=80, y0=70, x1=260, y1=95)),
            _block("date", "签字日期：2026.6.11", BBox(x0=90, y0=100, x1=220, y1=130)),
        ],
    )

    date_object = next(
        item
        for item in WholePageSigningVisualRecall().detect(document)
        if item.object_type == SigningVisualObjectType.HANDWRITTEN_DATE
    )
    _, diffs = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=[date_object]),
        [],
        [],
        start_index=1,
    )

    assert diffs[0].match_score_details["change_decision"] == "NEEDS_REVIEW"


def test_handwritten_date_outside_selected_region_is_recalled_near_party_anchor(tmp_path: Path) -> None:
    def draw(page: fitz.Page) -> None:
        shape = page.new_shape()
        shape.draw_polyline([(250, 250), (275, 235), (300, 255), (325, 235), (350, 255)])
        shape.finish(color=(0, 0, 0), width=4)
        shape.commit()

    path = _write_pdf(tmp_path, "handwritten-date.pdf", draw)
    document = _document(
        path,
        [
            _block("party-a", "甲方（盖章）：A公司", BBox(x0=80, y0=120, x1=260, y1=150)),
            _block("date", "2026.6.11日期：", BBox(x0=240, y0=225, x1=370, y1=265)),
        ],
    )

    dates = [
        item
        for item in WholePageSigningVisualRecall().detect(document)
        if item.object_type == SigningVisualObjectType.HANDWRITTEN_DATE
    ]

    assert len(dates) == 1
    assert dates[0].party_role == "甲方"
    assert dates[0].text == "2026.6.11"


def test_party_owned_signing_date_title_is_not_rewritten_as_global() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        source_type="signing_region",
        title="甲方 · 签字日期",
        readable_change="新增甲方 · 签字日期",
        match_score_details={
            "field_key": "date",
            "party_attribution_reason": "NEAREST_PARTY_ANCHOR",
        },
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=130, y0=500, x1=230, y1=535),
                highlight_type="ADD",
            )
        ],
    )

    WholePageSigningVisualRecall._correct_global_signing_dates([diff])

    assert diff.title == "甲方 · 签字日期"


def test_object_evidence_keeps_round_and_personal_seal_bboxes_separate() -> None:
    company = _object(
        SigningVisualObjectType.COMPANY_ROUND_SEAL,
        bbox=BBox(x0=100, y0=100, x1=220, y1=220),
    )
    personal = _object(
        SigningVisualObjectType.PERSONAL_NAME_SEAL,
        bbox=BBox(x0=235, y0=145, x1=290, y1=200),
    )
    result = SigningVisualRecallResult(additions=[company, personal])

    _, diffs = WholePageSigningVisualRecall().reconcile_diffs(result, [], [], start_index=1)

    assert len(diffs) == 2
    assert diffs[0].compare_evidence[0].bbox == company.bbox
    assert diffs[1].compare_evidence[0].bbox == personal.bbox


def test_visual_area_safety_net_is_review_only_and_not_duplicated() -> None:
    page = Page(
        page_no=1,
        width=600,
        height=800,
        blocks=[_block("sign", "甲方（盖章）：", BBox(x0=80, y0=100, x1=220, y1=130))],
    )
    bbox = BBox(x0=100, y0=150, x1=260, y1=260)

    candidate = WholePageSigningVisualRecall.safety_net_candidate(
        page,
        bbox,
        visual_delta=0.6,
        explained=False,
    )

    assert candidate is not None
    assert candidate.object_type == SigningVisualObjectType.UNKNOWN_SIGNING_MARK
    assert WholePageSigningVisualRecall.safety_net_candidate(page, bbox, visual_delta=0.6, explained=True) is None


def test_name_seal_fuses_same_object_across_seal_and_signing_channels() -> None:
    bbox = BBox(x0=214, y0=647, x1=272.5, y1=702)
    near = BBox(x0=214, y0=647.74, x1=274.67, y1=703.71)
    seal = DiffItem(diff_id="SEAL", diff_type="ADD", source_type="seal", compare_text="林高场",
                    compare_evidence=[EvidenceBox(page_no=7, bbox=bbox, highlight_type="ADD")])
    signature = DiffItem(diff_id="SIGN", diff_type="ADD", source_type="signing_region",
                         section_type="signature:signature", compare_text="已签",
                         compare_evidence=[EvidenceBox(page_no=7, bbox=near, highlight_type="ADD")])
    result = SigningVisualRecallResult(additions=[_object(SigningVisualObjectType.PERSONAL_NAME_SEAL,
                                                          page_no=7, bbox=near)])
    seals, signing = WholePageSigningVisualRecall().reconcile_diffs(result, [seal], [signature], start_index=3)
    assert len(seals + signing) == 1
    assert (seals + signing)[0].match_score_details["object_type"] == "PERSONAL_NAME_SEAL"
    assert {item["source_type"] for item in (seals + signing)[0].match_score_details["fused_object_evidence"]} == {
        "seal", "signing_region"
    }
    assert len(result.removed_duplicate_diff_ids) == 1


def test_name_seal_does_not_absorb_overlapping_larger_company_seal() -> None:
    bbox = BBox(x0=140, y0=140, x1=180, y1=180)
    large = DiffItem(diff_id="COMPANY", diff_type="ADD", source_type="seal", compare_text="公司合同专用章",
                     compare_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=100,y0=100,x1=220,y1=220), highlight_type="ADD")])
    result = SigningVisualRecallResult(additions=[_object(SigningVisualObjectType.PERSONAL_NAME_SEAL, bbox=bbox)])
    seals, signing = WholePageSigningVisualRecall().reconcile_diffs(result, [large], [], start_index=2)
    assert len(seals + signing) == 2
    assert seals[0].diff_id == "COMPANY"
    assert result.removed_duplicate_diff_ids == []


def test_name_seal_top_row_signature_interpretation_is_fused() -> None:
    visual = _object(SigningVisualObjectType.PERSONAL_NAME_SEAL,
                     bbox=BBox(x0=303.33,y0=121.28,x1=362,y1=178.59))
    signature = DiffItem(diff_id="SIGN", diff_type="ADD", source_type="signing_region",
                         section_type="signature:signature", compare_text="已签",
                         compare_evidence=[EvidenceBox(page_no=1,bbox=BBox(x0=303.5,y0=121,x1=360.5,y1=153),highlight_type="ADD")])
    result = SigningVisualRecallResult(additions=[visual])
    seals, signing = WholePageSigningVisualRecall().reconcile_diffs(result, [], [signature], start_index=2)
    assert len(seals + signing) == 1
    assert signing[0].match_score_details["object_type"] == "PERSONAL_NAME_SEAL"
    assert result.new_diff_ids == []
