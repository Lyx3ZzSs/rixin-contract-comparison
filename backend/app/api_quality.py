from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api_quality_schemas import (
    ExpectedDiffPatchRequest,
    QualityCaseDetailResponse,
    QualityCaseExportRequest,
    QualityCaseExportResponse,
    QualityCaseListResponse,
    QualityRegressionRequest,
    QualityRunRequest,
    QualityRunResponse,
    QualityTaskReviewResponse,
)
from app.config import settings
from app.services.quality_workbench import (
    InvalidQualityWorkbenchIdError,
    QualityCaseNotFoundError,
    QualityExpectedDiffNotFoundError,
    QualityTaskNotFoundError,
    QualityWorkbenchService,
)


router = APIRouter(prefix="/api/quality", tags=["quality"])


def get_quality_workbench_service() -> QualityWorkbenchService:
    backend_root = Path(__file__).resolve().parents[1]
    case_root = backend_root / "tests" / "fixtures" / "ocr_compare_cases"
    output_root = backend_root / ".ocr-compare-quality"
    return QualityWorkbenchService(
        case_root=case_root,
        task_root=settings.tasks_dir,
        output_root=output_root,
    )


@router.get("/cases", response_model=QualityCaseListResponse)
def list_cases(
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    return {"cases": service.list_cases()}


@router.post("/cases/export", response_model=QualityCaseExportResponse)
def export_case(
    request: QualityCaseExportRequest,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    try:
        return service.export_case(
            request.task_id,
            request.case_id,
            force=request.force,
        )
    except (
        InvalidQualityWorkbenchIdError,
        QualityTaskNotFoundError,
        FileExistsError,
    ) as exc:
        raise _quality_http_error(exc) from exc


@router.get("/tasks/{task_id:path}/review", response_model=QualityTaskReviewResponse)
def review_quality_task(
    task_id: str,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    try:
        return service.review_task(task_id)
    except (InvalidQualityWorkbenchIdError, QualityTaskNotFoundError) as exc:
        raise _quality_http_error(exc) from exc


@router.post("/evaluate", response_model=QualityRunResponse)
def evaluate_quality(
    request: QualityRunRequest,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    try:
        return service.evaluate_cases(
            dataset_splits=(
                set(request.dataset_splits)
                if request.dataset_splits is not None
                else None
            ),
            run_id=request.run_id,
        )
    except InvalidQualityWorkbenchIdError as exc:
        raise _quality_http_error(exc) from exc


@router.post("/regression", response_model=QualityRunResponse)
def run_quality_regression(
    request: QualityRegressionRequest,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    try:
        return service.run_regression(
            dataset_splits=(
                set(request.dataset_splits)
                if request.dataset_splits is not None
                else None
            ),
            baseline_name=request.baseline_name,
            run_id=request.run_id,
        )
    except InvalidQualityWorkbenchIdError as exc:
        raise _quality_http_error(exc) from exc


@router.get("/cases/{case_id:path}", response_model=QualityCaseDetailResponse)
def get_case(
    case_id: str,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    try:
        return service.get_case(case_id)
    except (InvalidQualityWorkbenchIdError, QualityCaseNotFoundError) as exc:
        raise _quality_http_error(exc) from exc


@router.patch(
    "/cases/{case_id}/expected-diffs/{index}",
    response_model=QualityCaseDetailResponse,
)
def update_expected_diff(
    case_id: str,
    index: int,
    request: ExpectedDiffPatchRequest,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    try:
        return service.update_expected_diff(
            case_id,
            index,
            request.model_dump(exclude_unset=True),
        )
    except (
        InvalidQualityWorkbenchIdError,
        QualityCaseNotFoundError,
        QualityExpectedDiffNotFoundError,
    ) as exc:
        raise _quality_http_error(exc) from exc


@router.post(
    "/cases/{case_id}/expected-diffs",
    response_model=QualityCaseDetailResponse,
)
def create_expected_diff(
    case_id: str,
    request: ExpectedDiffPatchRequest,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    try:
        return service.create_expected_diff(
            case_id,
            request.model_dump(exclude_unset=True),
        )
    except (InvalidQualityWorkbenchIdError, QualityCaseNotFoundError) as exc:
        raise _quality_http_error(exc) from exc


@router.delete(
    "/cases/{case_id}/expected-diffs/{index}",
    response_model=QualityCaseDetailResponse,
)
def delete_expected_diff(
    case_id: str,
    index: int,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    try:
        return service.delete_expected_diff(case_id, index)
    except (
        InvalidQualityWorkbenchIdError,
        QualityCaseNotFoundError,
        QualityExpectedDiffNotFoundError,
    ) as exc:
        raise _quality_http_error(exc) from exc


def _quality_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InvalidQualityWorkbenchIdError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(
        exc,
        (
            QualityCaseNotFoundError,
            QualityTaskNotFoundError,
            QualityExpectedDiffNotFoundError,
        ),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))
