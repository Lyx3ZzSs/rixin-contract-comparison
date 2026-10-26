"""Local, versioned event API. Uses the same task access policy as /api/compare."""

from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import Field
from app.api import _load_accessible_or_404, require_app_role
from app.api_schemas import DiffReviewRequest
from app.auth.dependencies import get_current_user
from app.auth.models import CurrentUser
from app.application.compare_tasks import default_compare_task_application
from app.services.diff_events import event_response
from app.services.review_service import CompareReviewService

router = APIRouter(prefix="/api/compare", tags=["events"])


@router.get("/{task_id}/events")
def get_events(task_id: str, user: CurrentUser = Depends(get_current_user)):
    return event_response(_load_accessible_or_404(task_id, user))


class EventReviewRequest(DiffReviewRequest):
    revision: int = Field(ge=0)
    seal_decision: Literal['CONFIRMED_CHANGE', 'IMPRESSION', 'RESET'] | None = None


@router.put("/{task_id}/events/{event_id}/review")
def review_event(
    task_id: str, event_id: str, request: EventReviewRequest, user: CurrentUser = Depends(require_app_role)
):
    _load_accessible_or_404(task_id, user, mutate=True)
    service = CompareReviewService(default_compare_task_application.repository)

    def mutate(task):
        if task.status != "COMPLETED" or task.revision != request.revision:
            raise HTTPException(409, "任务或审核版本已变化，请刷新后重试。")
        response = event_response(task)
        if request.seal_decision is not None:
            from app.services.seal_review import apply_seal_decision
            try:
                apply_seal_decision(task, event_id, request.seal_decision, response, service,
                                    user.display_name, request.review_comment)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            task.report_revision += 1
            return
        observation = next((o for o in response["comparison_observations"] if o["observation_id"] == event_id), None)
        if observation is not None:
            task.event_reviews[event_id] = service._review(
                request.review_status, request.review_comment, user.display_name,
                existing=task.event_reviews.get(event_id),
            )
            task.report_revision += 1
            return
        event = next((e for e in response["events"] if e["event_id"] == event_id), None)
        if event is None:
            raise HTTPException(404, "差异事件不存在。")
        if (
            event.get("evidence_selection")
            or "cell_selection" in event
            or "text_selection" in event
            or "field_selection" in event
            or "cell_selection_by_raw" in event
        ):
            task.event_reviews[event_id] = service._review(
                request.review_status,
                request.review_comment,
                user.display_name,
                existing=task.event_reviews.get(event_id),
            )
            task.report_revision += 1
            return
        normalized = service._normalized_reviews(task)
        for item_id in event["audit_item_ids"]:
            service._set_review(
                normalized,
                item_id,
                service._review(
                    request.review_status, request.review_comment, user.display_name, existing=normalized.get(item_id)
                ),
            )
        task.audit_item_reviews = normalized
        task.audit_item_reviews_normalized = True
        service._project_diff_reviews(task)
        service.refresh_review_stats(task)
        task.report_revision += 1

    updated = service.repository.update_compare_task(task_id, mutate)
    return event_response(updated)


@router.get("/{task_id}/events/export")
def export_events(task_id: str, user: CurrentUser = Depends(get_current_user)):
    import json

    task = _load_accessible_or_404(task_id, user)
    return Response(
        json.dumps(event_response(task), ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="events-{task_id}.json"'},
    )


@router.get("/{task_id}/events/report")
def event_report(task_id: str, user: CurrentUser = Depends(get_current_user)):
    import tempfile
    from pathlib import Path
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    from app.services.report_generator import ReportGenerator, build_report_filename

    task = _load_accessible_or_404(task_id, user)
    if task.status != "COMPLETED":
        raise HTTPException(409, "任务尚未完成。")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as file:
        path = Path(file.name)
    try:
        ReportGenerator().generate(task, path, event_view=True)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=build_report_filename(task),
        background=BackgroundTask(path.unlink, missing_ok=True),
    )
