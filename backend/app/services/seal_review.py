"""Explicit, reversible adjudication of one-sided seal impressions.

Ordinary review status remains an acknowledgement. Confirming an impression's
origin does not remove its page-level image change. Keep detector data for undo.
"""
from copy import deepcopy

from app.models import ComparisonObservation, DiffItem
from app.services.audit_summary import build_audit_items
from app.services.diff_events import build_snapshot, source_fingerprint


def apply_seal_decision(task, target_id, decision, response, review_service, reviewer, comment):
    observation = next((o for o in task.comparison_observations if o.observation_id == target_id), None)
    event = next((e for e in response['events'] if e['event_id'] == target_id), None)
    raw = None
    if event and len(event['raw_diff_ids']) == 1:
        raw = next(d for d in task.diffs if d.diff_id == event['raw_diff_ids'][0])
        linked = raw.match_score_details.get('seal_observation_id')
        if linked:
            observation = next((o for o in task.comparison_observations if o.observation_id == linked), None)
    obj = observation or raw
    if (obj is None or obj.source_type != 'seal'
            or bool(obj.original_evidence) == bool(obj.compare_evidence)
            or (raw is not None and raw.diff_type not in {'ADD', 'DELETE'})):
        raise ValueError('仅支持单侧印章印迹的变化裁决。')
    if decision == 'RESET' and observation is None:
        raise ValueError('该印章尚无可撤销的人工裁决。')
    snapshot = deepcopy(task.event_snapshot)
    if not snapshot or snapshot.get('source_fingerprint') != source_fingerprint(task):
        snapshot = build_snapshot(task, merge=False, page_alignment=response['page_alignment'])
    if observation is None:
        observation = ComparisonObservation(
            observation_id=f'seal-review-{raw.diff_id}', source_type='seal',
            reason='MANUAL_SEAL_REVIEW', title='印章印迹待复核',
            message='印章印迹需核验。', source_diff_ids=[raw.diff_id], source_diff=raw.model_copy(deep=True),
            original_evidence=[e.model_copy(update={'method':'comparison_observation','highlight_type':None}) for e in raw.original_evidence],
            compare_evidence=[e.model_copy(update={'method':'comparison_observation','highlight_type':None}) for e in raw.compare_evidence])
        task.comparison_observations.append(observation)
    # A recorded source preserves OCR and object identity. Legacy observations
    # have geometry only; never invent a reading of their seal text.
    source = observation.source_diff
    if source is None:
        direction = 'ADD' if observation.compare_evidence else 'DELETE'
        side = 'compare' if direction == 'ADD' else 'original'
        source = DiffItem(diff_id=f'reviewed-{observation.observation_id}', diff_type=direction,
            source_type='seal', title='印章印迹', quality_status='NEEDS_REVIEW',
            content_recognition_status='NEEDS_REVIEW', content_recognition_reason='SEAL_TEXT_UNVERIFIED',
            **{side+'_text':'检测到印章印迹（章文待辨认）', side+'_evidence':[
                e.model_copy(update={'method':'seal_region','highlight_type':direction})
                for e in getattr(observation, side+'_evidence')]})
        observation.source_diff = source.model_copy(deep=True)
    raw_id = source.diff_id
    existing = [d for d in task.diffs if d.diff_id == raw_id]
    if existing and not (raw is existing[0] or existing[0].match_score_details.get('seal_observation_id') == observation.observation_id):
        raise ValueError('印章来源已被其他差异占用，请重新核验。')
    normalized = review_service._normalized_reviews(task)
    old_ids = {a.item_id for a in build_audit_items(task.diffs) if a.diff_id == raw_id}
    for item_id in old_ids:
        normalized.pop(item_id, None)
    task.diffs = [d for d in task.diffs if d.diff_id != raw_id]
    removed_events = [e for e in snapshot['events'] if raw_id in e['raw_diff_ids']]
    snapshot['events'] = [e for e in snapshot['events'] if raw_id not in e['raw_diff_ids']]
    for entry in removed_events:
        task.event_reviews.pop(entry['event_id'], None)
    resolution = None if decision == 'RESET' else decision
    observation.change_resolution = resolution
    status = 'UNREVIEWED' if decision == 'RESET' else 'CONFIRMED'
    review = review_service._review(status, comment, reviewer,
                                   existing=task.event_reviews.get(observation.observation_id))
    task.event_reviews[observation.observation_id] = review
    # Reset restores the machine result, including an original counted candidate.
    restore_candidate = decision == 'RESET' and observation.reason == 'MANUAL_SEAL_REVIEW'
    if decision in {'CONFIRMED_CHANGE', 'IMPRESSION'} or restore_candidate:
        promoted = source.model_copy(deep=True)
        promoted.match_score_details['seal_observation_id'] = observation.observation_id
        if decision in {'CONFIRMED_CHANGE', 'IMPRESSION'}:
            promoted.match_score_details['seal_change_decision'] = 'CONFIRMED'
        if observation.decision_evidence and not promoted.match_score_details.get('impression_evidence'):
            promoted.match_score_details['impression_evidence'] = [deepcopy(observation.decision_evidence)]
        if decision == 'IMPRESSION':
            promoted.match_score_details['seal_origin_decision'] = 'CONFIRMED_IMPRESSION'
            promoted.title = '印章印迹（人工确认为透印）'
            promoted.readable_change = '人工确认为透印；本页印章印迹变化仍计入差异，不代表独立盖章。'
        promoted.review_status = status
        task.diffs.append(promoted)
        for audit in build_audit_items([promoted]):
            normalized[audit.item_id] = review
        entries = build_snapshot(task.model_copy(update={'diffs':[promoted]}), merge=False)['events']
        snapshot['events'].extend(entries)
        if restore_candidate:
            # The detector candidate is once again the active record.
            promoted.match_score_details.pop('seal_observation_id', None)
            task.comparison_observations.remove(observation)
    task.audit_item_reviews = normalized
    task.audit_item_reviews_normalized = True
    task.diff_count = len(task.diffs)
    snapshot['raw_diff_count'] = len(task.diffs)
    snapshot['source_fingerprint'] = source_fingerprint(task, version=snapshot['version'])
    snapshot.setdefault('counting_steps', []).append({
        'step':'manual_seal_decision','observation_id':observation.observation_id,
        'decision':decision,'event_count':len(snapshot['events'])})
    task.event_snapshot = snapshot
    review_service.refresh_review_stats(task)
