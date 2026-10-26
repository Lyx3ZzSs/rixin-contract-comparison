"""Bounded transport for structure-only table inference; failures are regional."""

from __future__ import annotations

import base64
from io import BytesIO
import threading
from typing import Literal

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings, settings


class TableStructureError(ValueError):
    pass


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    label: Literal["wired", "wireless"]
    score: float = Field(ge=0, le=1)
    scores: list[float] = Field(min_length=2, max_length=2)


class ModelCell(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    row: int = Field(ge=0, le=511)
    column: int = Field(ge=0, le=511)
    rowspan: int = Field(ge=0, le=512)
    colspan: int = Field(ge=0, le=512)
    bbox: list[float] = Field(min_length=4, max_length=4)
    polygon: list[list[float]] = Field(min_length=4, max_length=4)
    raw_geometry_index: int = Field(ge=0)


class StructureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1]
    request_id: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    classification: Classification
    branch: Literal["wired", "wireless"]
    model: Literal["UNet+TableRecover", "SLANet_plus"]
    weight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_version: Literal["mineru-4.0.10"]
    coordinate_space: Literal["CROP_PIXELS"]
    padding: Literal[0, 64, 128]
    rotated_fix: bool
    cells: list[ModelCell] = Field(max_length=4096)
    elapsed_seconds: float = Field(ge=0)
    additional_ocr_requests: Literal[0]
    transforms: dict = Field(default_factory=dict)


class TableStructureClient:
    _lock = threading.Lock()
    _limits: dict[int, threading.BoundedSemaphore] = {}

    def __init__(self, config: Settings = settings, client: httpx.Client | None = None):
        self.config, self.client = config, client
        with self._lock:
            self.limit = self._limits.setdefault(
                config.table_structure_max_inflight, threading.BoundedSemaphore(config.table_structure_max_inflight)
            )

    def predict(self, image: Image.Image, request_id: str, *, branch="auto", padding=0) -> dict:
        endpoint = self.config.table_structure_endpoint.rstrip("/")
        if not endpoint:
            raise TableStructureError("TABLE_STRUCTURE_ENDPOINT_UNAVAILABLE")
        if not endpoint.endswith("/table-structure"):
            endpoint += "/table-structure"
        data = BytesIO()
        image.save(data, format="PNG")
        body = dict(
            schema_version=1,
            request_id=request_id,
            image=base64.b64encode(data.getvalue()).decode("ascii"),
            width=image.width,
            height=image.height,
            branch=branch,
            padding=padding,
        )
        timeout = self.config.table_structure_timeout
        if not self.limit.acquire(timeout=timeout):
            raise TableStructureError("TABLE_STRUCTURE_QUEUE_TIMEOUT")
        try:
            if self.client is None:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(endpoint, json=body)
            else:
                response = self.client.post(endpoint, json=body, timeout=timeout)
            response.raise_for_status()
            result = StructureResponse.model_validate(response.json())
            if (
                result.request_id != request_id
                or (result.width, result.height) != image.size
                or result.padding != padding
                or (branch != "auto" and result.branch != branch)
                or (branch == "auto" and result.branch != result.classification.label)
                or (result.branch == "wired" and (not result.rotated_fix or result.model != "UNet+TableRecover"))
                or (result.branch == "wireless" and (result.model != "SLANet_plus" or padding))
            ):
                raise TableStructureError("TABLE_STRUCTURE_RESPONSE_MISMATCH")
            return result.model_dump(mode="json")
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            raise TableStructureError(f"TABLE_STRUCTURE_SERVICE_ERROR: {type(exc).__name__}") from exc
        finally:
            self.limit.release()
