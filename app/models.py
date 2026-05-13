from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


DiffType = Literal["ADD", "DELETE", "MODIFY"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
TaskStatus = Literal["PROCESSING", "COMPLETED", "FAILED"]


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    def expanded(self, amount: float, width: float, height: float) -> "BBox":
        return BBox(
            x0=max(0, self.x0 - amount),
            y0=max(0, self.y0 - amount),
            x1=min(width, self.x1 + amount),
            y1=min(height, self.y1 + amount),
        )


class EvidenceBox(BaseModel):
    page_no: int
    bbox: BBox
    method: str = "clause_fallback"
    text: str = ""


class TextBlock(BaseModel):
    block_id: str
    page_no: int
    text: str
    bbox: BBox


class Page(BaseModel):
    page_no: int
    width: float
    height: float
    blocks: list[TextBlock] = Field(default_factory=list)


class Document(BaseModel):
    filename: str
    path: str
    page_count: int
    pages: list[Page] = Field(default_factory=list)


class Clause(BaseModel):
    clause_id: str
    clause_no: str = ""
    title: str = ""
    text: str
    normalized_text: str
    page_numbers: list[int] = Field(default_factory=list)
    bboxes: list[EvidenceBox] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)


class ClausePair(BaseModel):
    original: Clause | None = None
    compare: Clause | None = None
    score: float = 0
    match_method: str = "unmatched"


class AIAnalysis(BaseModel):
    risk_level: RiskLevel = "LOW"
    risk_score: int = Field(default=20, ge=0, le=100)
    contract_element: str = "一般条款"
    change_summary: str = "未发现重大风险。"
    risk_explanation: str = "该差异需要结合业务背景复核。"
    review_suggestion: str = "建议由合同经办人与法务共同确认。"
    raw_response: dict[str, Any] | None = None

    @field_validator("risk_level", mode="before")
    @classmethod
    def normalize_risk_level(cls, value: str) -> str:
        value = str(value).upper()
        if value not in {"LOW", "MEDIUM", "HIGH"}:
            raise ValueError("risk_level must be LOW, MEDIUM, or HIGH")
        return value


class DiffItem(BaseModel):
    diff_id: str
    diff_type: DiffType
    original_clause_id: str | None = None
    compare_clause_id: str | None = None
    clause_no: str = ""
    title: str = ""
    original_text: str = ""
    compare_text: str = ""
    original_snippet: str = ""
    compare_snippet: str = ""
    readable_change: str = ""
    original_evidence: list[EvidenceBox] = Field(default_factory=list)
    compare_evidence: list[EvidenceBox] = Field(default_factory=list)
    ai_analysis: AIAnalysis | None = None
    original_screenshot: str = ""
    compare_screenshot: str = ""


class CompareTask(BaseModel):
    task_id: str
    status: TaskStatus = "PROCESSING"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    original_filename: str = ""
    compare_filename: str = ""
    original_pdf_path: str = ""
    compare_pdf_path: str = ""
    original_highlight_pdf_path: str = ""
    compare_highlight_pdf_path: str = ""
    report_pdf_path: str = ""
    diff_count: int = 0
    high_risk_count: int = 0
    medium_risk_count: int = 0
    low_risk_count: int = 0
    ai_summary: str = ""
    diffs: list[DiffItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
