from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.services.model_routing import RouteRecommendation


OcrRetryStatus = Literal["SKIPPED", "SUCCEEDED", "FAILED"]


class OcrRetryRequest(BaseModel):
    task_id: str
    side: Literal["original", "compare"]
    page_no: int
    pdf_path: Path
    route: RouteRecommendation
    reason_codes: list[str] = Field(default_factory=list)


class OcrRetryResult(BaseModel):
    status: OcrRetryStatus
    reason: str
    side: Literal["original", "compare"]
    page_no: int
    route: RouteRecommendation
    changed_output: bool = False
    metrics: dict[str, object] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class OcrRetryAdapter(Protocol):
    def retry_page(self, request: OcrRetryRequest) -> OcrRetryResult:
        """Retry OCR for one page or region."""


class NoopOcrRetryAdapter:
    def retry_page(self, request: OcrRetryRequest) -> OcrRetryResult:
        return OcrRetryResult(
            status="SKIPPED",
            reason="RETRY_DISABLED",
            side=request.side,
            page_no=request.page_no,
            route=request.route,
            changed_output=False,
            notes=["OCR retry execution is disabled in Phase 4A."],
        )
