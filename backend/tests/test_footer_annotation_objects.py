"""Physical footer objects must survive unreadable OCR and fragmented ownership."""
import numpy as np
from app.models import BBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.footer_annotation_visual import FooterAnnotationVisualComparator


def test_missing_counter_does_not_restrict_search_to_known_right_footer():
    page = Page(page_no=1, width=595, height=842, blocks=[TextBlock(
        block_id='right', page_no=1, text='签名', block_type='footer',
        bbox=BBox(x0=440, y0=780, x1=510, y1=825))])
    left, right, top, bottom = FooterAnnotationVisualComparator._footer_search_region(page)
    assert left <= .03 and right >= .95


def test_dense_curved_pen_strokes_are_not_scan_border_columns():
    import cv2
    mask = np.zeros((1263, 893), np.uint8)
    cv2.polylines(mask, [np.array([[45,1235],[53,1198],[60,1220],[67,1203],[72,1240]])], False, 255, 3)
    mask[100:1250, 20:23] = 255  # Detached scan border, still excluded.
    boxes = FooterAnnotationVisualComparator()._candidate_components(mask, search_region=(.025,.97,.94,.995))
    assert any(b.x0 < 60 and b.x1 > 65 and b.y1 > 1230 for b in boxes)
    assert not any(b.x0 <= 23 for b in boxes)


def test_physical_footer_objects_split_pages_merge_nested_boxes_and_keep_readings(tmp_path):
    import fitz
    from app.services.footer_annotation_objects import reconcile_footer_objects
    path = tmp_path/'ink.pdf'
    pdf = fitz.open()
    for _ in range(3):
        p = pdf.new_page(width=595, height=842)
        p.draw_polyline([(35,810),(42,780),(47,811),(55,785),(60,813)],color=(0,0,0),width=2)
    pdf.save(path); pdf.close()
    doc = Document(filename='ink.pdf', path=str(path), page_count=3,
                   pages=[Page(page_no=n,width=595,height=842,blocks=[]) for n in range(1,4)])
    box = BBox(x0=30,y0=777,x1=64,y1=817)
    def e(n, method, text=''):
        return EvidenceBox(page_no=n,bbox=box,method=method,text=text,confidence=.9,highlight_type='ADD')
    text = DiffItem(diff_id='ocr',diff_type='ADD',source_type='header_footer',compare_text='亲',
                   compare_evidence=[e(n,'header_footer','亲') for n in range(1,4)],text_confidence=.1)
    ink = DiffItem(diff_id='ink',diff_type='ADD',source_type='header_footer',
                   review_flags=['VISUAL_FOOTER_ANNOTATION'],content_recognition_reason='VISUAL_FOOTER_CONTENT_UNVERIFIED',
                   compare_evidence=[e(n,'footer_visual_registered') for n in range(1,4)])
    nested = text.model_copy(deep=True); nested.diff_id='fragment'; nested.compare_text='妥'
    nested.compare_evidence=[e(2,'header_footer','妥')]
    nested.compare_evidence[0].bbox=BBox(x0=40,y0=790,x1=47,y1=805)
    result = reconcile_footer_objects([text,ink,nested],doc,doc)
    assert len(result)==3
    assert [d.title for d in result]==[f'第{n}页左下角手写签注' for n in range(1,4)]
    assert all(d.compare_text=='检测到左下角手写签注' for d in result)
    assert all(len(d.compare_evidence)==1 and not d.compare_evidence[0].char_boxes for d in result)
    assert all(d.match_score_details['recognition_scope']=='annotation_presence' for d in result)
    assert any(c['text']=='亲' for c in result[0].content_candidates)
    assert reconcile_footer_objects(result,doc,doc)==result


def test_pen_touching_scan_frame_is_not_removed_with_whole_connected_frame():
    import cv2
    mask=np.zeros((1263,893),np.uint8)
    cv2.rectangle(mask,(0,0),(892,1262),255,2)
    cv2.line(mask,(0,1259),(42,1230),255,2)
    cv2.polylines(mask,[np.array([[40,1234],[48,1203],[55,1230],[65,1208]])],False,255,3)
    boxes=FooterAnnotationVisualComparator()._candidate_components(mask,search_region=(.025,.97,.94,.995))
    assert any(b.x0<55 and b.x1>60 and b.y0<1210 for b in boxes)


def test_one_off_or_faint_visual_footer_is_not_promoted_to_handwriting(tmp_path):
    import fitz
    from app.services.footer_annotation_objects import reconcile_footer_objects
    path=tmp_path/'faint.pdf';pdf=fitz.open()
    for _ in range(3):
        page=pdf.new_page(width=595,height=842)
        page.draw_polyline([(35,810),(42,780),(47,811),(55,785),(60,813)],color=(.75,.75,.75),width=2)
    pdf.save(path);pdf.close()
    doc=Document(filename='faint.pdf',path=str(path),page_count=3,
                 pages=[Page(page_no=n,width=595,height=842,blocks=[]) for n in range(1,4)])
    ds=[DiffItem(diff_id=f'ink-{n}',diff_type='ADD',source_type='header_footer',
        compare_evidence=[EvidenceBox(page_no=n,bbox=BBox(x0=30,y0=777,x1=64,y1=817),method='footer_visual_registered')]) for n in range(1,4)]
    assert reconcile_footer_objects(ds,doc,doc)==ds
    assert reconcile_footer_objects(ds[:1],doc,doc)==ds[:1]


def test_low_confidence_footer_reading_is_not_confirmed():
    from app.services.recognition_evidence import retain_recognition_evidence
    b=BBox(x0=25,y0=800,x1=55,y1=837)
    block=TextBlock(block_id='low',page_no=1,text='亲',bbox=b,confidence=.12)
    doc=Document(filename='x',path='x',page_count=1,pages=[Page(page_no=1,width=595,height=842,blocks=[block])])
    diff=DiffItem(diff_id='low',diff_type='ADD',source_type='header_footer',compare_text='亲',
                  compare_evidence=[EvidenceBox(page_no=1,bbox=b,text='亲',method='header_footer')])
    retain_recognition_evidence([diff],doc,doc)
    assert diff.content_recognition_status=='NEEDS_REVIEW'
