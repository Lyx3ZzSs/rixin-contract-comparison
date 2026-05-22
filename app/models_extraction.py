from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import TaskStatus

ExtractionFieldStatus = Literal["found", "not_found", "error"]


class ExtractionFieldDef(BaseModel):
    id: str
    name: str
    type: str = "文本"
    description: str = ""
    semantic_extraction: bool = True


class ExtractionFieldValue(BaseModel):
    field_id: str
    field_name: str
    value: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_snippet: str = ""
    status: ExtractionFieldStatus = "not_found"


class ExtractionTask(BaseModel):
    task_id: str
    task_type: str = "extraction"
    status: TaskStatus = "PROCESSING"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    filename: str = ""
    file_path: str = ""
    converted_file_path: str = ""
    extractor_used: str = ""
    raw_result_path: str = ""
    fields: list[ExtractionFieldDef] = Field(default_factory=list)
    results: list[ExtractionFieldValue] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
