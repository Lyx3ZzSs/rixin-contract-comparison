from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.signing_region.extractor import SigningRegionExtractor
from app.services.signing_region.models import (
    SigningElement,
    SigningElementType,
    SigningBlock,
    SigningBlockConfidenceLevel,
    SigningBlockRole,
    SigningRegion,
    SigningRegionRole,
)


def test_signing_region_model_holds_elements_and_reasons() -> None:
    element = SigningElement(
        element_id="E1",
        element_type=SigningElementType.SEAL,
        page_no=1,
        bbox=BBox(x0=100, y0=650, x1=180, y1=730),
        text="合同专用章",
        confidence=0.9,
        source="layout",
    )
    region = SigningRegion(
        region_id="SR-1-1",
        page_no=1,
        bbox=BBox(x0=80, y0=630, x1=220, y1=760),
        region_role=SigningRegionRole.PARTY_A,
        confidence=0.92,
        confidence_reasons=["seal_block", "signing_label"],
        elements=[element],
    )

    assert region.elements[0].element_type == SigningElementType.SEAL
    assert region.region_role == SigningRegionRole.PARTY_A
    assert "seal_block" in region.confidence_reasons


def _bbox(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _block(
    block_id: str,
    text: str,
    bbox: BBox,
    *,
    block_type: str = "text",
    page_no: int = 1,
    layout_bbox: BBox | None = None,
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=text,
        bbox=bbox,
        block_type=block_type,
        layout_bbox=layout_bbox,
    )


def _document(blocks: list[TextBlock], *, page_no: int = 1) -> Document:
    return Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=1,
        pages=[Page(page_no=page_no, width=595, height=842, blocks=blocks)],
    )


def test_date_component_ref_locates_only_filled_day_value() -> None:
    text = "2026年5月6日"
    source = TextBlock(
        block_id="date",
        page_no=1,
        text=text,
        bbox=BBox(x0=100, y0=700, x1=210, y1=720),
        char_boxes=[
            CharBox(
                char=char,
                page_no=1,
                bbox=BBox(x0=100 + index * 10, y0=700, x1=110 + index * 10, y1=720),
                text_index=index,
            )
            for index, char in enumerate(text)
        ],
    )

    ref = SigningRegionExtractor._date_component_ref(source)

    assert ref["date_components"] == {"year": "2026", "month": "5", "day": "6"}
    assert BBox.model_validate(ref["date_component_bboxes"]["day"]) == BBox(
        x0=170,
        y0=700,
        x1=180,
        y1=720,
    )


def test_date_component_ref_preserves_blank_day_slot() -> None:
    source = TextBlock(
        block_id="date",
        page_no=1,
        text="2026 年5月 日",
        bbox=BBox(x0=100, y0=700, x1=220, y1=720),
    )

    ref = SigningRegionExtractor._date_component_ref(source)

    assert ref["date_components"] == {"year": "2026", "month": "5", "day": ""}
    assert "day" not in ref["date_component_bboxes"]


def test_date_component_ref_recovers_value_bbox_from_table_ocr_chars() -> None:
    structured_text = "签订时间：2026年5月158"
    ocr_text = "签订时间：2026年5月15日"
    source = TextBlock(
        block_id="date-table-cell",
        page_no=13,
        text=structured_text,
        bbox=BBox(x0=80, y0=480, x1=310, y1=508),
        source="signing_table_cell",
        char_boxes=[
            CharBox(
                char=char,
                page_no=13,
                bbox=BBox(x0=90 + index * 10, y0=482, x1=100 + index * 10, y1=505),
                text_index=334 + index,
            )
            for index, char in enumerate(ocr_text)
        ],
    )

    ref = SigningRegionExtractor._date_component_ref(source)

    assert ref["date_components"] == {"year": "2026", "month": "5", "day": "15"}
    assert BBox.model_validate(ref["date_value_bbox"]) == BBox(
        x0=140,
        y0=482,
        x1=240,
        y1=505,
    )


def test_date_component_ref_recovers_value_bbox_when_table_and_ocr_month_disagree() -> None:
    structured_text = "签订时间：2026年5月150"
    ocr_text = "签订时间：2026年51月15日"
    source = TextBlock(
        block_id="date-table-cell",
        page_no=13,
        text=structured_text,
        bbox=BBox(x0=300, y0=480, x1=512, y1=508),
        source="signing_table_cell",
        char_boxes=[
            CharBox(
                char=char,
                page_no=13,
                bbox=BBox(x0=310 + index * 10, y0=482, x1=320 + index * 10, y1=505),
                text_index=350 + index,
            )
            for index, char in enumerate(ocr_text)
        ],
    )

    ref = SigningRegionExtractor._date_component_ref(source)

    assert ref["date_components"] == {"year": "2026", "month": "5", "day": "15"}
    assert BBox.model_validate(ref["date_value_bbox"]) == BBox(
        x0=360,
        y0=482,
        x1=470,
        y1=505,
    )


def test_date_component_ref_does_not_apply_ocr_suffix_repair_outside_table_cells() -> None:
    source = TextBlock(
        block_id="plain-text",
        page_no=1,
        text="签订时间：2026年5月10",
        bbox=BBox(x0=100, y0=700, x1=260, y1=720),
    )

    assert SigningRegionExtractor._date_component_ref(source) == {}


def test_extractor_detects_seal_label_and_date_region() -> None:
    doc = _document(
        [
            _block("label", "甲方（盖章）：", _bbox(60, 650, 170, 675)),
            _block("seal", "合同专用章", _bbox(80, 680, 190, 780), block_type="seal"),
            _block("date", "签订日期：2026年5月6日", _bbox(60, 790, 240, 815)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    assert {element.element_type.value for element in regions[0].elements} >= {"seal", "label", "date_field"}
    assert regions[0].confidence >= 0.7


def test_extractor_rejects_keyword_only_body_text() -> None:
    doc = _document(
        [
            _block(
                "body",
                "13.2 对本合同的修改以双方签章的书面协议为准。甲方应当配合乙方履行义务。",
                _bbox(60, 680, 520, 720),
            ),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert regions == []


def test_extractor_rejects_single_bottom_keyword() -> None:
    doc = _document(
        [
            _block("footer_word", "甲方", _bbox(60, 760, 90, 780)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert regions == []


def test_extractor_detects_form_like_signature_page_without_seal_block() -> None:
    doc = _document(
        [
            _block("context", "以下无正文，为签署页", _bbox(60, 520, 240, 545)),
            _block("party_a", "甲方：__________    乙方：__________", _bbox(60, 650, 460, 675)),
            _block("sign", "授权代表（签字）：__________", _bbox(60, 700, 280, 725)),
            _block("date", "日期：____年__月__日", _bbox(60, 750, 260, 775)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    assert regions[0].confidence >= 0.5


def test_extractor_detects_no_seal_signature_form_with_short_date_placeholder() -> None:
    doc = _document(
        [
            _block("party_a", "甲方：__________    乙方：__________", _bbox(60, 650, 460, 675)),
            _block("sign", "授权代表（签字）：__________", _bbox(60, 700, 280, 725)),
            _block("date", "日期：__年__月__日", _bbox(60, 750, 260, 775)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    assert "date_field" in {element.element_type.value for element in regions[0].elements}
    assert regions[0].confidence >= 0.5


def test_extractor_detects_straddling_seal_using_effective_bbox() -> None:
    doc = _document(
        [
            _block("label", "甲方（盖章）：", _bbox(60, 650, 170, 675)),
            _block(
                "seal",
                "合同专用章",
                _bbox(80, 500, 190, 620),
                block_type="seal",
                layout_bbox=_bbox(80, 500, 190, 680),
            ),
            _block("date", "签订日期：2026年5月6日", _bbox(60, 700, 240, 725)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    assert "seal" in {element.element_type.value for element in regions[0].elements}
    assert regions[0].confidence >= 0.7


def test_extractor_normalizes_out_of_page_region_bbox() -> None:
    doc = _document(
        [
            _block("label", "甲方（盖章）：", _bbox(800, 650, 900, 675)),
            _block("seal", "合同专用章", _bbox(820, 680, 920, 780), block_type="seal"),
            _block("date", "签订日期：2026年5月6日", _bbox(800, 790, 980, 815)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    bbox = regions[0].bbox
    page = doc.pages[0]
    assert 0 <= bbox.x0 <= bbox.x1 <= page.width
    assert 0 <= bbox.y0 <= bbox.y1 <= page.height


def test_extractor_builds_role_scoped_standard_and_custom_fields() -> None:
    blocks = [
        _block("left", "地址：北京市海淀区 送达邮箱：a@example.com", _bbox(60, 650, 270, 700)),
        _block("right", "地址：上海市浦东新区 送达邮箱：b@example.com", _bbox(320, 650, 540, 700)),
    ]
    signing_block = SigningBlock(
        block_id="SB-1",
        page_no=1,
        bbox=_bbox(40, 620, 555, 760),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        source_block_ids=["left", "right"],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document(blocks))[0]
    fields = [element for element in region.elements if element.element_type == SigningElementType.FIELD]

    assert {(field.raw_ref["party_role"], field.raw_ref["field_key"]) for field in fields} == {
        ("甲方", "address"),
        ("甲方", "email"),
        ("乙方", "address"),
        ("乙方", "email"),
    }
    assert len({(field.bbox.x0, field.bbox.x1) for field in fields}) == 4
    assert all(BBox.model_validate(field.raw_ref["value_bbox"]).x0 > field.bbox.x0 for field in fields)


def test_extractor_classifies_signature_date_as_date_field() -> None:
    date = _block("date", "签字日期：2026年05月22日", _bbox(60, 650, 240, 675))
    signing_block = SigningBlock(
        block_id="SB-1",
        page_no=1,
        bbox=_bbox(40, 620, 555, 760),
        block_role=SigningBlockRole.PARTY_A,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        source_block_ids=["date"],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document([date]))[0]
    field = next(element for element in region.elements if element.element_type == SigningElementType.FIELD)

    assert field.raw_ref["field_key"] == "date"
    assert field.text == "2026年05月22日"


def test_extractor_splits_two_addresses_merged_into_one_ocr_line() -> None:
    party_a = _block("party-a", "甲方：南京瑞尚电力科技有限公司", _bbox(60, 123, 268, 139))
    party_b = _block("party-b", "乙方：国能日新科技股份有限公司", _bbox(289, 120, 502, 139))
    address_line = _block(
        "addresses",
        "地址：江苏省南京市栖霞区八卦洲街地址：北京市",
        _bbox(58, 180, 385, 198),
    )
    left_continuation = _block("left-address", "道鹂岛路254号悦福大厦12-0044", _bbox(60, 212, 266, 228))
    right_continuation = _block("right-address", "路27号1幢", _bbox(299, 211, 385, 228))
    signing_block = SigningBlock(
        block_id="SB-9",
        page_no=1,
        bbox=_bbox(40, 120, 520, 360),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["two_column_layout"],
        source_block_ids=["party-a", "party-b", "addresses", "right-address", "left-address"],
    )

    region = SigningRegionExtractor().extract_from_blocks(
        [signing_block],
        _document([party_a, party_b, address_line, right_continuation, left_continuation]),
    )[0]
    fields = [element for element in region.elements if element.element_type == SigningElementType.FIELD]

    assert [
        (field.raw_ref["party_role"], field.raw_ref["field_key"], field.raw_ref["field_label"], field.text)
        for field in fields
    ] == [
        ("甲方", "address", "地址", "江苏省南京市栖霞区八卦洲街道鹂岛路254号悦福大厦12-0044"),
        ("乙方", "address", "地址", "北京市路27号1幢"),
    ]
    assert all(len(field.raw_ref["value_bboxes"]) == 2 for field in fields)
    assert [field.raw_ref["field_segments"] for field in fields] == [
        ["地址：江苏省南京市栖霞区八卦洲街", "道鹂岛路254号悦福大厦12-0044"],
        ["地址：北京市", "路27号1幢"],
    ]
    assert all(field.raw_ref["field_prefix"] == "地址：" for field in fields)


def test_extractor_keeps_merged_fields_in_their_columns_and_joins_credit_code_lines() -> None:
    blocks = [
        _block("left-contact", "联系人：环加飞", _bbox(94, 342, 193, 357)),
        _block("right-contact", "联系人：刘玉良", _bbox(314, 341, 413, 356)),
        _block(
            "emails",
            "Email:huan.jiafei@nc.sgcc.com.cn Email:yuliang.liu@sprixin.com",
            _bbox(97, 413, 499, 429),
        ),
        _block(
            "credit-codes",
            "统一社会信用代码：911100000536统一社会信用代码：91110108672",
            _bbox(94, 436, 506, 450),
        ),
        _block("left-credit-tail", "21038D", _bbox(94, 459, 140, 474)),
        _block("right-credit-tail", "3891430", _bbox(314, 459, 365, 474)),
    ]
    signing_block = SigningBlock(
        block_id="SB-24",
        page_no=1,
        bbox=_bbox(72, 279, 525, 490),
        block_role=SigningBlockRole.UNKNOWN,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["two_column_layout"],
        source_block_ids=[block.block_id for block in blocks],
    )

    extractor = SigningRegionExtractor()
    assert 280 < extractor._two_column_midpoint(signing_block, blocks) < 330
    region = extractor.extract_from_blocks([signing_block], _document(blocks))[0]
    fields = {
        (element.raw_ref["party_role"], element.raw_ref["field_key"]): element.text
        for element in region.elements
        if element.element_type == SigningElementType.FIELD and element.raw_ref["field_key"] in {"email", "credit_code"}
    }

    assert fields == {
        ("甲方", "email"): "huan.jiafei@nc.sgcc.com.cn",
        ("乙方", "email"): "yuliang.liu@sprixin.com",
        ("甲方", "credit_code"): "91110000053621038D",
        ("乙方", "credit_code"): "911101086723891430",
    }


def test_extractor_does_not_treat_signing_page_context_as_signature_field() -> None:
    context = _block("context", "签字页，此页无正文", _bbox(60, 75, 167, 90))
    signing_block = SigningBlock(
        block_id="SB-9",
        page_no=1,
        bbox=_bbox(40, 60, 520, 240),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        source_block_ids=["context"],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document([context]))[0]

    assert not any(element.element_type == SigningElementType.FIELD for element in region.elements)


def test_extractor_builds_fields_for_standalone_truncated_signing_labels() -> None:
    blocks = [
        _block("left", "法人", _bbox(90, 106, 123, 126)),
        _block("right", "法人代表或", _bbox(334, 108, 400, 125)),
        _block("tax", "纳税人识别", _bbox(334, 140, 410, 158)),
    ]
    signing_block = SigningBlock(
        block_id="SB-1",
        page_no=1,
        bbox=_bbox(57, 53, 524, 191),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.85,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        source_block_ids=["left", "right", "tax"],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document(blocks))[0]
    fields = [element for element in region.elements if element.element_type == SigningElementType.FIELD]

    assert [
        (field.raw_ref["party_role"], field.raw_ref["field_key"], field.raw_ref["field_label"]) for field in fields
    ] == [
        ("甲方", "legal_representative", "法人"),
        ("乙方", "legal_representative", "法人代表或"),
        ("乙方", "credit_code", "纳税人识别"),
    ]


def test_extractor_preserves_parentheses_on_standalone_signature_label() -> None:
    signature = _block("signature", "(签字)", _bbox(180, 690, 210, 707))
    signing_block = SigningBlock(
        block_id="SB-1",
        page_no=1,
        bbox=_bbox(57, 620, 524, 740),
        block_role=SigningBlockRole.PARTY_A,
        confidence=0.85,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        source_block_ids=["signature"],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document([signature]))[0]
    field = next(element for element in region.elements if element.element_type == SigningElementType.FIELD)

    assert field.raw_ref["field_key"] == "signature"
    assert field.raw_ref["field_label"] == "(签字)"
    assert field.raw_ref["field_prefix"] == "(签字)"


def test_extractor_does_not_turn_combined_party_names_into_custom_fields() -> None:
    party_line = _block(
        "parties",
        "甲方：江苏东大金智信息系统有限公司乙方：国能日新科技股份有限公司",
        _bbox(90, 60, 500, 81),
    )
    signing_block = SigningBlock(
        block_id="SB-1",
        page_no=1,
        bbox=_bbox(57, 53, 524, 191),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.85,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        source_block_ids=["parties"],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document([party_line]))[0]

    assert not any(element.element_type == SigningElementType.FIELD for element in region.elements)


def test_extractor_joins_split_party_name_and_stamp_suffix_in_merged_party_row() -> None:
    blocks = [
        _block(
            "parties",
            "甲方：国家电网有限公司华北分部乙方：国能日新科技股份有限公",
            _bbox(90, 134, 505, 150),
        ),
        _block("party-b-tail", "司", _bbox(310, 158, 327, 175)),
        _block("stamp-a", "(盖章)", _bbox(94, 181, 144, 198)),
        _block("stamp-b", "(盖章)", _bbox(313, 181, 364, 198)),
    ]
    signing_block = SigningBlock(
        block_id="SB-25",
        page_no=1,
        bbox=_bbox(70, 70, 520, 500),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["paired_parties", "two_column_layout"],
        source_block_ids=[block.block_id for block in blocks],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document(blocks))[0]
    parties = {
        element.raw_ref["party_role"]: element
        for element in region.elements
        if element.element_type == SigningElementType.PARTY_FIELD
    }

    assert parties["甲方"].text == "甲方：国家电网有限公司华北分部(盖章)"
    assert parties["甲方"].raw_ref["field_segments"] == ["甲方：国家电网有限公司华北分部", "(盖章)"]
    assert parties["乙方"].text == "乙方：国能日新科技股份有限公司(盖章)"
    assert parties["乙方"].raw_ref["field_segments"] == ["乙方：国能日新科技股份有限公", "司", "(盖章)"]
    assert len(parties["甲方"].raw_ref["field_bboxes"]) == 2
    assert len(parties["乙方"].raw_ref["field_bboxes"]) == 3


def test_extractor_builds_reliable_signing_fields_from_aggregated_table_html() -> None:
    table = TextBlock(
        block_id="table",
        page_no=1,
        text="单位名称（章）：国能日新科技股份有限公司\n单位地址：北京市海淀区\n法人代表：张三\n邮政编码：100096",
        raw_html=(
            "<table><tr><td>单位名称（章）：国能日新科技股份有限公司 单位地址：北京市海淀区</td></tr>"
            "<tr><td>法人代表：张三</td></tr><tr><td>邮 政 编 码：100096</td></tr></table>"
        ),
        bbox=_bbox(60, 400, 520, 700),
        block_type="table",
        table_cell_bboxes=[
            [60, 400, 520, 470],
            [60, 470, 520, 520],
            [60, 520, 520, 570],
        ],
    )
    signing_block = SigningBlock(
        block_id="SB-1",
        page_no=1,
        bbox=_bbox(40, 380, 540, 720),
        confidence=0.85,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        source_block_ids=["table"],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document([table]))[0]
    fields = [element for element in region.elements if element.element_type == SigningElementType.FIELD]

    assert [(field.raw_ref["field_key"], field.text) for field in fields] == [
        ("party_name", "国能日新科技股份有限公司"),
        ("address", "北京市海淀区"),
        ("legal_representative", "张三"),
        ("postal_code", "100096"),
    ]
    assert [(field.bbox.y0, field.bbox.y1) for field in fields] == [
        (400, 470),
        (400, 470),
        (470, 520),
        (520, 570),
    ]


def test_extractor_uses_geometry_for_two_column_block_mislabeled_as_party_a() -> None:
    blocks = [
        _block("left", "电话：025-12345678", _bbox(60, 650, 240, 675)),
        _block("right", "电话：", _bbox(320, 650, 380, 675)),
    ]
    signing_block = SigningBlock(
        block_id="SB-1",
        page_no=1,
        bbox=_bbox(40, 620, 555, 760),
        block_role=SigningBlockRole.PARTY_A,
        confidence=0.7,
        confidence_level=SigningBlockConfidenceLevel.MEDIUM,
        confidence_reasons=["party_label", "two_column_layout"],
        source_block_ids=["left", "right"],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document(blocks))[0]
    fields = [element for element in region.elements if element.element_type == SigningElementType.FIELD]

    assert [(field.raw_ref["party_role"], field.text) for field in fields] == [
        ("甲方", "025-12345678"),
        ("乙方", ""),
    ]


def test_extractor_infers_two_columns_when_scan_ocr_misses_party_b_label() -> None:
    blocks = [
        _block("left-address", "地址：随州市淅河镇", _bbox(60, 220, 270, 250)),
        _block("right-address", "地址：北京市海淀区", _bbox(320, 220, 540, 250)),
        _block("left-postal", "邮编：441300", _bbox(60, 260, 270, 290)),
        _block("right-postal", "邮编：100096", _bbox(320, 260, 540, 290)),
    ]
    signing_block = SigningBlock(
        block_id="SB-SCAN",
        page_no=1,
        bbox=_bbox(40, 100, 555, 500),
        block_role=SigningBlockRole.PARTY_A,
        confidence=0.8,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["party_label"],
        source_block_ids=[block.block_id for block in blocks],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document(blocks))[0]
    fields = [element for element in region.elements if element.element_type == SigningElementType.FIELD]

    assert "two_column_layout" in region.confidence_reasons
    assert {(field.raw_ref["party_role"], field.raw_ref["field_key"]) for field in fields} == {
        ("甲方", "address"),
        ("乙方", "address"),
        ("甲方", "postal_code"),
        ("乙方", "postal_code"),
    }


def test_extractor_uses_shared_table_border_as_two_column_midpoint() -> None:
    table = TextBlock(
        block_id="table",
        page_no=1,
        text="地址：随州市淅河镇\n地址：北京市海淀区\n邮编：441300\n邮编：100096",
        bbox=_bbox(66, 180, 523, 300),
        block_type="table",
        raw_html=(
            "<table><tr><td>地址：随州市淅河镇</td><td>地址：北京市海淀区</td></tr>"
            "<tr><td>邮编：441300</td><td>邮编：100096</td></tr></table>"
        ),
        table_cell_bboxes=[
            [66, 180, 307.1, 240],
            [307, 180, 523, 240],
            [66, 240, 307.1, 300],
            [307, 240, 523, 300],
        ],
    )
    signing_block = SigningBlock(
        block_id="SB-TABLE",
        page_no=1,
        bbox=_bbox(46, 140, 543, 320),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["paired_parties", "two_column_layout"],
        source_block_ids=["table"],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document([table]))[0]
    fields = [element for element in region.elements if element.element_type == SigningElementType.FIELD]

    assert [(field.raw_ref["party_role"], field.text) for field in fields] == [
        ("甲方", "随州市淅河镇"),
        ("乙方", "北京市海淀区"),
        ("甲方", "441300"),
        ("乙方", "100096"),
    ]


def test_extractor_rejects_noisy_table_edge_as_column_midpoint() -> None:
    cells = [
        _block("left-name", "单位名称：甲公司 单位地址：北京", _bbox(58, 420, 315, 457)),
        _block("right-name", "单位名称：乙公司 单位地址：青海", _bbox(293, 421, 536, 459)),
        _block("left-address", "27号", _bbox(60, 455, 311, 482)),
        _block("right-address", "1号楼", _bbox(274, 450, 536, 487)),
    ]
    for cell in cells:
        cell.source = "signing_table_cell"
    signing_block = SigningBlock(
        block_id="SB-NOISY-TABLE",
        page_no=1,
        bbox=_bbox(37, 242, 556, 687),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["two_column_layout"],
        source_block_ids=[cell.block_id for cell in cells],
    )

    midpoint = SigningRegionExtractor._two_column_midpoint(signing_block, cells)

    assert midpoint == 296.5


def test_extractor_uses_table_char_boxes_for_multiline_field_bbox() -> None:
    text = "单位名称：甲公司单位地址：北京市海淀区"
    chars = [
        CharBox(
            char=char,
            page_no=1,
            bbox=_bbox(60 + index * 10, 420 if index < 8 else 450, 69 + index * 10, 462 if index < 8 else 462),
            text_index=index,
        )
        for index, char in enumerate(text)
    ]
    table = TextBlock(
        block_id="table",
        page_no=1,
        text=text,
        bbox=_bbox(58, 418, 315, 465),
        block_type="table",
        raw_html="<table><tr><td>单位名称：甲公司 单位地址：北京市海淀区</td></tr></table>",
        table_cell_bboxes=[[58, 418, 315, 465]],
        char_boxes=chars,
    )

    cell = SigningRegionExtractor._expand_table_field_blocks([table])[0]
    address_start = cell.text.index("单位地址")
    bbox = SigningRegionExtractor._field_bbox(cell, address_start, len(cell.text))

    assert bbox.y0 == 450
    assert bbox.x0 >= 140


def test_extractor_uses_table_char_boxes_for_address_continuation_bbox() -> None:
    address = _block("address", "单位地址：北京市海淀区", _bbox(60, 420, 315, 458))
    continuation = _block("continuation", "创业孵化基地1号楼0814室", _bbox(59, 449, 315, 487))
    for block in (address, continuation):
        block.source = "signing_table_cell"
    continuation.char_boxes = [
        CharBox(char=char, page_no=1, bbox=_bbox(90 + index * 8, 466, 97 + index * 8, 475), text_index=index)
        for index, char in enumerate(continuation.text)
    ]
    signing_block = SigningBlock(
        block_id="SB-ADDRESS",
        page_no=1,
        bbox=_bbox(40, 400, 555, 520),
        block_role=SigningBlockRole.PARTY_A,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        source_block_ids=[address.block_id, continuation.block_id],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document([address, continuation]))[0]
    field = next(element for element in region.elements if element.raw_ref.get("field_key") == "address")
    continuation_bbox = BBox.model_validate(field.raw_ref["field_bboxes"][1])

    assert continuation_bbox == _bbox(90, 466, 201, 475)


def test_extractor_infers_stacked_party_rows_and_standalone_dates() -> None:
    blocks = [
        _block("a-sign", "法定代表人（负责人）：", _bbox(90, 170, 360, 195)),
        _block("a-date", "2026年5月8日", _bbox(110, 210, 330, 238)),
        _block("b-sign", "法定代表人（负责人）：", _bbox(90, 350, 360, 375)),
        _block("b-date", "2026年5月8日", _bbox(110, 390, 330, 418)),
    ]
    signing_block = SigningBlock(
        block_id="SB-STACKED",
        page_no=1,
        bbox=_bbox(70, 90, 410, 430),
        block_role=SigningBlockRole.PARTY_A,
        confidence=0.8,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["party_label"],
        source_block_ids=[block.block_id for block in blocks],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document(blocks))[0]
    fields = [element for element in region.elements if element.element_type == SigningElementType.FIELD]

    assert "stacked_party_layout" in region.confidence_reasons
    assert [(field.raw_ref["party_role"], field.raw_ref["field_key"], field.text) for field in fields] == [
        ("甲方", "legal_representative", ""),
        ("乙方", "legal_representative", ""),
        ("甲方", "date", "2026年5月8日"),
        ("乙方", "date", "2026年5月8日"),
    ]


def test_extractor_uses_field_columns_when_region_bbox_is_inflated_and_keeps_ocr_signature() -> None:
    blocks = [
        _block("right-party-residual", "盖章）", _bbox(500, 330, 550, 346)),
        _block("left-sign", "授权代表签字：", _bbox(68, 360, 126, 378)),
        _block("signature", "刘万程", _bbox(493, 348, 572, 385)),
        _block("left-phone", "电话：025-69833061", _bbox(68, 430, 180, 444)),
        _block("right-phone", "电话：", _bbox(289, 429, 328, 444)),
        _block("left-bank", "开户行：中行江宁开发区支行", _bbox(69, 448, 209, 460)),
        _block("right-bank", "开户行：", _bbox(290, 446, 332, 460)),
    ]
    signing_block = SigningBlock(
        block_id="SB-7",
        page_no=1,
        bbox=_bbox(48, 321, 597, 660),
        block_role=SigningBlockRole.PARTY_A,
        confidence=0.7,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["party_label", "two_column_layout"],
        source_block_ids=[block.block_id for block in blocks],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document(blocks))[0]
    fields = [element for element in region.elements if element.element_type == SigningElementType.FIELD]
    signatures = [element for element in region.elements if element.element_type == SigningElementType.SIGNATURE]
    residuals = [
        element
        for element in region.elements
        if element.element_type == SigningElementType.LABEL and element.raw_ref.get("residual_kind") == "party_suffix"
    ]

    assert {(field.raw_ref["party_role"], field.raw_ref["field_key"]) for field in fields} >= {
        ("甲方", "phone"),
        ("乙方", "phone"),
        ("甲方", "bank"),
        ("乙方", "bank"),
    }
    assert [(signature.raw_ref["party_role"], signature.text, signature.bbox) for signature in signatures] == [
        ("乙方", "刘万程", _bbox(493, 348, 572, 385))
    ]
    assert [(residual.raw_ref["party_role"], residual.text) for residual in residuals] == [("乙方", "盖章）")]


def test_extractor_does_not_treat_truncated_printed_signature_label_as_handwritten_name() -> None:
    blocks = [
        _block("label", "法定代表人或授权代表签字：", _bbox(320, 190, 470, 208)),
        _block("label-fragment", "法定代表人或授", _bbox(78, 188, 164, 206), block_type="seal"),
        _block("phone", "电话：029-61825538", _bbox(80, 380, 212, 397)),
    ]
    signing_block = SigningBlock(
        block_id="SB-13",
        page_no=1,
        bbox=_bbox(60, 70, 544, 551),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["paired_parties", "two_column_layout"],
        source_block_ids=[block.block_id for block in blocks],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document(blocks))[0]

    assert not [element for element in region.elements if element.element_type == SigningElementType.SIGNATURE]
    assert [
        (
            element.raw_ref["party_role"],
            element.raw_ref["field_key"],
            element.raw_ref["field_label"],
            element.text,
        )
        for element in region.elements
        if element.element_type == SigningElementType.FIELD
        and element.raw_ref.get("source_block_ids") == ["label-fragment"]
    ] == [("甲方", "legal_representative", "法定代表人或授权代表签字", "")]


def test_extractor_does_not_parse_seal_html_as_signing_field() -> None:
    seal = _block(
        "seal",
        '<div style="text-align: center;"></div>',
        _bbox(80, 650, 180, 750),
        block_type="seal",
    )
    signing_block = SigningBlock(
        block_id="SB-1",
        page_no=1,
        bbox=_bbox(40, 620, 555, 780),
        confidence=0.85,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        source_block_ids=["seal"],
    )

    region = SigningRegionExtractor().extract_from_blocks([signing_block], _document([seal]))[0]

    assert not any(element.element_type == SigningElementType.FIELD for element in region.elements)
