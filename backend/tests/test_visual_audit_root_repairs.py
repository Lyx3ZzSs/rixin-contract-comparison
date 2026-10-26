from app.models import BBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.diff_quality import DiffQualityProcessor
from app.services.source_consensus import located_blank_form_equivalence
from app.services.document_role_repair import reconcile_body_roles


def test_percentage_underlines_do_not_change_value_but_changed_numbers_remain():
    processor = DiffQualityProcessor()
    for a, b, equal in [
        ("合同总价_2__%", "合同总价2%", True),
        ("合同总价__2_%", "合同总价3%", False),
        ("应付1_000元", "应付1000元", False),
        ("比例1_000%", "比例1000%", False),
        ("合同编号AB_CD", "合同编号ABCD", False),
        ("甲方执__肆____份", "甲方执肆份", True),
        ("甲方执__肆____份", "甲方执伍份", False),
        ("甲方执____份", "甲方执份", False),
        ("罚款-2%", "罚款2%", False),
    ]:
        diff = DiffItem(
            diff_id="d",
            diff_type="MODIFY",
            source_type="clause",
            original_text=a,
            compare_text=b,
            review_flags=["CRITICAL_FIELD_CHANGE"],
        )
        assert processor._looks_like_form_separator_equivalent(diff) == equal


def test_empty_form_reconciliation_requires_same_location_and_small_punctuation():
    def block(id, text, x, y, w, h):
        return TextBlock(block_id=id, text=text, page_no=1, bbox=BBox(x0=x, y0=y, x1=x + w, y1=y + h))

    original = Document(
        filename="o.pdf",
        path="o.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[block("a", "（四）其他：/。", 100, 510, 200, 16)])],
    )
    compare = Document(
        filename="c.pdf",
        path="c.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[block("b", "（四）其他：", 98, 510, 60, 16), block("dot", "o", 275, 521, 4, 4)],
            )
        ],
    )
    diff = DiffItem(
        diff_id="d",
        diff_type="ADD",
        source_type="clause",
        compare_text="(四)其他:\no",
        compare_evidence=[EvidenceBox(page_no=1, bbox=compare.pages[0].blocks[0].bbox)],
    )
    assert located_blank_form_equivalence(diff, original, compare)
    compare.pages[0].blocks[1].bbox.y0 = 510
    assert not located_blank_form_equivalence(diff, original, compare)
    diff.compare_text = "(四)其他:新增服务"
    assert not located_blank_form_equivalence(diff, original, compare)


def test_same_located_midpage_table_caption_gets_same_ownership():
    def doc(side, kind):
        return Document(
            filename=side + ".pdf",
            path=side + ".pdf",
            page_count=1,
            pages=[
                Page(
                    page_no=2,
                    width=595,
                    height=842,
                    blocks=[
                        TextBlock(
                            block_id=side + "anchor",
                            page_no=2,
                            text="本项目的服务范围如下所示",
                            bbox=BBox(x0=80, y0=240, x1=350, y1=260),
                        ),
                        TextBlock(
                            block_id=side + "caption",
                            page_no=2,
                            text="工作量清单表",
                            block_type=kind,
                            bbox=BBox(x0=95, y0=294, x1=192, y1=310),
                        ),
                        TextBlock(
                            block_id=side + "table",
                            page_no=2,
                            text="服务内容",
                            block_type="table",
                            bbox=BBox(x0=70, y0=330, x1=520, y1=450),
                        ),
                    ],
                )
            ],
        )

    a, b = doc("a", "paragraph_title"), doc("b", "text")
    reconcile_body_roles(a, b)
    assert b.pages[0].blocks[1].block_type == "paragraph_title"


def test_date_projection_cannot_swallow_changed_dispute_resolution_choice():
    d = DiffItem(
        diff_id="d",
        diff_type="MODIFY",
        source_type="clause",
        original_text="甲方、乙方争议解决采用第一种方式。签订日期：年月日",
        compare_text="甲方、乙方争议解决采用第二种方式。签订日期：2026年5月6日",
        original_snippet="一",
        compare_snippet="二2026年5月6日",
        review_flags=["CRITICAL_FIELD_CHANGE", "CRITICAL_FIELD_DATE_CHANGE"],
    )
    parts = DiffQualityProcessor()._split_mixed_choice_date([d], [])
    assert len(parts) == 2
    assert parts[1].original_text == "第一种方式" and parts[1].compare_text == "第二种方式"
    assert DiffQualityProcessor()._reclassify_signing_date_field_change(parts[0])
    assert parts[0].source_type == "metadata" and parts[1].source_type == "clause"


def test_printed_recall_uses_own_reading_and_consumes_incomplete_label(tmp_path):
    import fitz
    import numpy as np
    from app.services.printed_text_recall import recover_printed_signing_lines

    image = np.full((400, 400, 3), 255, np.uint8)
    image[120:180, 80:170] = [230, 80, 80]
    path = tmp_path / "scan.pdf"
    with fitz.open() as pdf:
        p = pdf.new_page(width=400, height=400)
        pix = fitz.Pixmap(fitz.csRGB, 400, 400, image.tobytes(), False)
        p.insert_image(p.rect, pixmap=pix)
        pdf.save(path)

    def doc(text):
        return Document(
            filename=path.name,
            path=str(path),
            page_count=1,
            pages=[
                Page(
                    page_no=1,
                    width=400,
                    height=400,
                    blocks=[
                        TextBlock(
                            block_id="party", page_no=1, text="甲方：某公司", bbox=BBox(x0=40, y0=80, x1=200, y1=96)
                        ),
                        TextBlock(block_id="label", page_no=1, text=text, bbox=BBox(x0=80, y0=150, x1=220, y1=166)),
                    ],
                )
            ],
        )

    class LocalOCR:
        value = "法定代表人（负责人）或"

        def predict(self, *args, **kwargs):
            return {}

        def payload_to_document(self, *args):
            return Document(
                filename="crop.png",
                path="crop.png",
                page_count=1,
                pages=[
                    Page(
                        page_no=1,
                        width=900,
                        height=318,
                        blocks=[
                            TextBlock(
                                block_id="own",
                                page_no=1,
                                text=self.value,
                                confidence=0.99,
                                bbox=BBox(x0=240, y0=135, x1=660, y1=183),
                            )
                        ],
                    )
                ],
            )

    reference, target = doc("法定代表人（负责人）或"), doc("法定代表（负责人）")
    ocr = LocalOCR()
    changed = recover_printed_signing_lines(reference, target, ocr)
    assert len(changed) == 1 and len(target.pages[0].blocks) == 2
    assert target.pages[0].blocks[1].text == ocr.value
    assert target.pages[0].blocks[1].source_evidence[0].raw_text == ocr.value
    ocr.value = "授权代表"
    target = doc("法定代表（负责人）")
    assert recover_printed_signing_lines(reference, target, ocr) == []
    assert target.pages[0].blocks[1].text == "法定代表（负责人）"
