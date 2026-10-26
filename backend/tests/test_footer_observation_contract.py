from app.models import AuditItemReview, BBox, CompareTask, DiffItem, EvidenceBox
from app.services.comparison_observations import separate_observations
from app.services.diff_events import event_response


def test_unclassified_footer_ink_is_reviewable_but_not_counted_as_a_change():
    task = CompareTask(task_id='footer', diffs=[DiffItem(
        diff_id='ink', diff_type='ADD', source_type='header_footer',
        content_recognition_reason='VISUAL_FOOTER_CONTENT_UNVERIFIED',
        compare_text='检测到视觉变化', compare_evidence=[EvidenceBox(
            page_no=4, bbox=BBox(x0=100, y0=790, x1=150, y1=825), method='footer_visual_registered',
            highlight_type='ADD')])])
    task.event_reviews['observation-ink'] = AuditItemReview(review_status='NEEDS_REVIEW', review_comment='核查透印')
    response = event_response(task)
    assert response['event_count'] == 0
    observation = response['comparison_observations'][0]
    assert observation['title'] == '页脚印迹待复核'
    assert observation['compare_evidence'][0]['highlight_type'] is None
    assert observation['review_comment'] == '核查透印'
    assert len(task.diffs) == 1
    separate_observations(task)
    persisted = CompareTask.model_validate_json(task.model_dump_json())
    assert not persisted.diffs and len(persisted.comparison_observations) == 1
    assert event_response(persisted)['event_count'] == 0


def test_local_ocr_evidence_can_own_visual_ink_in_a_long_multipage_footer():
    from app.services.footer_evidence_fusion import fuse_footer_evidence
    box = BBox(x0=440, y0=790, x1=490, y1=825)
    text = DiffItem(diff_id='ocr', diff_type='ADD', source_type='header_footer', compare_text='黄科 ' * 53,
        compare_evidence=[EvidenceBox(page_no=n, bbox=box, text='黄科', method='header_footer', highlight_type='ADD') for n in range(1, 54)])
    visual = DiffItem(diff_id='ink', diff_type='ADD', source_type='header_footer',
        review_flags=['VISUAL_FOOTER_ANNOTATION'], content_recognition_reason='VISUAL_FOOTER_CONTENT_UNVERIFIED',
        compare_evidence=[EvidenceBox(page_no=14, bbox=box, method='footer_visual_registered', highlight_type='ADD')])
    assert len(fuse_footer_evidence([text, visual])) == 1
