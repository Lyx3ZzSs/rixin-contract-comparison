"""Regression: known printed counters must constrain visual footer recall."""
import cv2
import numpy as np

from app.models import BBox, Page, TextBlock
from app.services.footer_annotation_visual import FooterAnnotationVisualComparator, PageRegistration, RenderedPage
from app.services.diff_quality import DiffQualityProcessor
from app.services.evidence_locator import EvidenceLocator


class Identity:
    def register(self, *args):
        return PageRegistration(np.eye(3, dtype=np.float32), 100, 90)


def sample(extra=None, text='共9页第9页', confidence=1.0):
    a = np.full((820, 600, 3), 255, np.uint8)
    b = a.copy()
    cv2.putText(a, '9 / 9', (270, 763), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 1)
    cv2.putText(b, '9 / 9', (270, 749), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 1)
    if extra == 'adjacent':
        cv2.polylines(b, [np.array([[330, 735], [345, 751], [330, 773], [356, 769]], np.int32)], False, (0, 0, 0), 2)
    if extra == 'overlap':
        cv2.polylines(b, [np.array([[280, 727], [297, 763], [310, 728]], np.int32)], False, (0, 0, 0), 2)
    def page(kind, value, y):
        return Page(page_no=9, width=600, height=820, blocks=[TextBlock(
            block_id=kind, page_no=9, text=value, block_type=kind, block_role='page_footer',
            confidence=confidence, bbox=BBox(x0=269,y0=y,x1=313,y1=y+15))])
    return a, b, page('number','共9页第9页',750), page('footer',text,736)


def run(extra=None, text='共9页第9页', confidence=1.0):
    a,b,pa,pb=sample(extra,text,confidence)
    return FooterAnnotationVisualComparator(registrar=Identity())._page_diffs(
        RenderedPage(a,600,820),RenderedPage(b,600,820),page_no=9,start_index=1,original_model=pa,compare_model=pb)


def test_shifted_equal_counter_is_not_a_visual_change():
    assert run() == []


def test_adjacent_added_ink_survives_counter_reconciliation_as_unverified():
    diffs=run('adjacent')
    assert len(diffs)==1 and diffs[0].diff_type=='ADD'
    assert all('手写' not in d.title+d.compare_text for d in diffs)
    assert diffs[0].content_recognition_status=='NEEDS_REVIEW'
    assert any(e.bbox.x1>=350 for e in diffs[0].compare_evidence)
    assert len(DiffQualityProcessor().process(diffs).diffs)==1


def test_overlapping_added_strokes_are_not_erased_with_counter_box():
    diffs=run('overlap')
    assert any(e.bbox.y0<733 or e.bbox.y1>754 for d in diffs for e in d.compare_evidence)
    assert all('手写' not in d.title for d in diffs)


def test_unreadable_opposite_is_not_treated_as_verified_blank():
    diffs=run(text='', confidence=.3)
    assert diffs and all(d.content_recognition_status=='NEEDS_REVIEW' for d in diffs)
    assert all('手写' not in d.title for d in diffs)


def test_shared_counter_rule_ignores_layout_type_difference():
    _,_,a,b=sample()
    svc=FooterAnnotationVisualComparator()
    # Equal geometry/role/text, different layout classes must yield equal search.
    b.blocks[0].bbox=a.blocks[0].bbox.model_copy()
    assert svc._footer_search_region(a)==svc._footer_search_region(b)


def test_visual_geometry_confidence_survives_evidence_assignment():
    d=run('adjacent')[0]
    before=d.compare_evidence[0].confidence
    EvidenceLocator().assign_evidence_confidence([d])
    assert d.compare_evidence[0].confidence==before
    assert d.text_confidence is None


def test_changed_counter_is_not_declared_equal_and_text_change_is_retained():
    from app.models import Document
    from app.services.header_footer_compare import HeaderFooterComparator
    _,_,a,b=sample(text='共10页第9页')
    a8=a.model_copy(deep=True);a8.page_no=8;a8.blocks[0].page_no=8;a8.blocks[0].text='共9页第8页'
    b8=b.model_copy(deep=True);b8.page_no=8;b8.blocks[0].page_no=8;b8.blocks[0].text='共10页第8页'
    original=Document(filename='a.pdf',path='a.pdf',page_count=9,pages=[a8,a])
    compare=Document(filename='b.pdf',path='b.pdf',page_count=9,pages=[b8,b])
    service=HeaderFooterComparator()
    assert service.matched_page_numbers(original,compare)[9] == []
    assert any(d.diff_type=='MODIFY' for d in service.build_diffs(original,compare))


def test_low_confidence_or_disputed_page_number_is_not_used_to_explain_ink():
    from app.services.page_number_evidence import match_page_counters
    _,_,a,b=sample(confidence=.3)
    assert match_page_counters(a,b)==[]


def test_visual_flag_alone_does_not_exempt_page_number_noise():
    from app.models import DiffItem
    d=DiffItem(diff_id='D001',diff_type='ADD',source_type='header_footer',
               compare_text='共9页第9页',compare_snippet='共9页第9页',review_flags=['VISUAL_FOOTER_ANNOTATION'])
    assert DiffQualityProcessor().process([d]).diffs==[]


def test_removing_unrelated_visual_candidate_does_not_reintroduce_ocr_fragment():
    from app.models import DiffItem, EvidenceBox
    def box(page):
        return EvidenceBox(page_no=page,bbox=BBox(x0=580,y0=420,x1=595,y1=435),method='seal_region')
    seal=DiffItem(diff_id='D1',diff_type='ADD',source_type='seal',compare_text='骑缝章',compare_evidence=[box(1),box(2)])
    fragment=DiffItem(diff_id='D2',diff_type='ADD',source_type='clause',compare_text='二',compare_snippet='二',compare_evidence=[box(2)])
    visual=DiffItem(diff_id='D3',diff_type='ADD',source_type='header_footer',compare_text='待复核',
                   review_flags=['VISUAL_FOOTER_ANNOTATION'],content_recognition_reason='VISUAL_FOOTER_CONTENT_UNVERIFIED',
                   content_recognition_status='NEEDS_REVIEW',compare_evidence=[box(9)])
    svc=FooterAnnotationVisualComparator()
    with_visual=svc.remove_overlapping_diffs([seal,fragment,visual])
    without=svc.remove_overlapping_diffs([seal,fragment])
    assert [d.diff_id for d in with_visual if d.diff_id!='D3']==[d.diff_id for d in without]==['D1']


def test_equal_counter_survives_different_render_scale():
    for scale in (.9, 1.1):
        a,b,pa,pb=sample()
        resized=cv2.resize(b,(round(600*scale),round(820*scale)))
        diffs=FooterAnnotationVisualComparator(registrar=Identity())._page_diffs(
            RenderedPage(a,600,820),RenderedPage(resized,600,820),page_no=9,start_index=1,
            original_model=pa,compare_model=pb)
        assert diffs==[]


def test_split_native_counter_includes_only_adjacent_decorative_dashes():
    from app.models import Page, TextBlock, BBox
    from app.services.page_number_evidence import page_counters
    def block(id, text, x, end, y=790):
        return TextBlock(block_id=id, page_no=1, text=text, confidence=1,
                         bbox=BBox(x0=x, y0=y, x1=end, y1=y+14))
    page=Page(page_no=1,width=595,height=842,blocks=[block('l','—',280,290),block('n','24',290,305),block('r','—',305,315),block('other','—',450,460)])
    counters=page_counters(page)
    assert len(counters)==1
    assert counters[0].text=='—24—'
    assert counters[0].bbox.x0==280 and counters[0].bbox.x1==315
    assert page.blocks[1].text=='24'
