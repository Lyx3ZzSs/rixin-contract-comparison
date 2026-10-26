"""Keep structural uncertainty visible without claiming a content change."""
from app.models import CompareTask, ComparisonObservation, DiffItem

REASONS = {"TABLE_STRUCTURE_UNRESOLVED", "TABLE_PRESENCE_UNKNOWN", "REGION_READING_UNCERTAIN"}


def is_unresolved_table(diff: DiffItem) -> bool:
    return diff.source_type == "table" and diff.match_score_details.get("decision_reason") in REASONS


def is_unresolved_change(diff: DiffItem) -> bool:
    return is_unresolved_table(diff) or (
        diff.source_type == "header_footer"
        and diff.content_recognition_reason == "VISUAL_FOOTER_CONTENT_UNVERIFIED"
    )


def _region_evidence(evidence):
    unique = {}
    for box in evidence:
        key = (box.page_no, box.bbox.x0, box.bbox.y0, box.bbox.x1, box.bbox.y1)
        if key not in unique:
            unique[key] = box.model_copy(update={"method": "comparison_observation", "highlight_type": None, "char_boxes": []})
    return list(unique.values())


def observations_for_task(task: CompareTask) -> list[ComparisonObservation]:
    """Read-only adapter for both persisted observations and legacy failure diffs."""
    from app.services.audit_summary import build_task_audit_items
    audits = build_task_audit_items(task) if any(is_unresolved_change(d) for d in task.diffs) else []
    active_seal_observations = {d.match_score_details.get('seal_observation_id') for d in task.diffs
                                if d.source_type == 'seal'}
    result = {o.observation_id: o.model_copy(deep=True) for o in task.comparison_observations
              if o.change_resolution != 'CONFIRMED_CHANGE' and o.observation_id not in active_seal_observations}
    for diff in task.diffs:
        if not is_unresolved_change(diff):
            continue
        observation_id = f"observation-{diff.diff_id}"
        review = {k: getattr(diff, k) for k in ("review_status", "review_comment", "reviewed_by", "reviewed_at")}
        audited = [a for a in audits if a.diff_id == diff.diff_id and a.review_status != "UNREVIEWED"]
        if audited:
            latest = max(audited, key=lambda a: a.reviewed_at)
            review = {k: getattr(latest, k) for k in review}
            if len({a.review_status for a in audited}) > 1:
                review["review_status"] = "NEEDS_REVIEW"
        # Preserve historical event/audit review decisions without rewriting history.
        old_event_ids = [e["event_id"] for e in (task.event_snapshot or {}).get("events", []) if diff.diff_id in e["raw_diff_ids"]]
        old_reviews = [task.event_reviews[i] for i in old_event_ids if i in task.event_reviews]
        if old_reviews:
            review = max(old_reviews, key=lambda r: r.reviewed_at).model_dump()
        result.setdefault(observation_id, ComparisonObservation(
            observation_id=observation_id, source_type=diff.source_type, reason=(diff.match_score_details["decision_reason"] if is_unresolved_table(diff)
                    else diff.content_recognition_reason),
            **({} if is_unresolved_table(diff) else {
                "title": "页脚印迹待复核",
                "message": "此处存在图像变化，尚不能确认是新增笔迹、透印还是扫描残影；不计入差异数。",
            }),
            **({"title": "区域文字识读待复核",
                "message": "区域已建立对应关系，但局部文字识读证据不足，暂不确认内容增删。"}
               if diff.match_score_details.get('decision_reason') == 'REGION_READING_UNCERTAIN' else {}),
            source_diff_ids=[diff.diff_id], original_evidence=_region_evidence(diff.original_evidence),
            compare_evidence=_region_evidence(diff.compare_evidence), **review,
        ))
    for observation in result.values():
        if observation.change_resolution == 'IMPRESSION':
            observation.title = '印章印迹（已确认为透印）'
            observation.message = '人工确认为透印，保留印迹供追溯，不计入独立印章增删。'
        review = task.event_reviews.get(observation.observation_id)
        if review:
            for key, value in review.model_dump().items():
                setattr(observation, key, value)
    return list(result.values())


def separate_observations(task: CompareTask) -> None:
    # A rerun must replace prior structural warnings, including when it recovers.
    current = task.model_copy(update={"comparison_observations": []})
    task.comparison_observations = observations_for_task(current)
    task.diffs = [d for d in task.diffs if not is_unresolved_change(d)]


def visible_snapshot_events(task: CompareTask, snapshot: dict) -> list[dict]:
    unresolved = {d.diff_id for d in task.diffs if is_unresolved_change(d)}
    return [e for e in snapshot["events"] if not unresolved.intersection(e["raw_diff_ids"])]
