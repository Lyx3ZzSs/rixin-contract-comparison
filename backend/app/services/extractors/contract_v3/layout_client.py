"""Single-page PP-DocLayoutV3 transport. Batch semantics are deliberately disabled."""

from __future__ import annotations

import base64
import threading
from typing import Any

import httpx

from app.config import Settings, settings
from app.services.extractors.base import DocumentExtractionError


class PPDocLayoutV3Client:
    _lock = threading.Lock()
    _limit: threading.BoundedSemaphore | None = None
    _limit_size: int | None = None

    def __init__(self, config: Settings = settings, client: httpx.Client | None = None) -> None:
        self.config = config
        self.client = client
        with self._lock:
            if type(self)._limit is None or type(self)._limit_size != config.ppdoclayout_max_inflight:
                type(self)._limit = threading.BoundedSemaphore(config.ppdoclayout_max_inflight)
                type(self)._limit_size = config.ppdoclayout_max_inflight

    def predict_page(self, png: bytes) -> list[dict[str, Any]]:
        endpoint = self.config.ppdoclayout_endpoint.rstrip("/")
        if not endpoint:
            raise DocumentExtractionError("未配置 PPDOCLAYOUT_ENDPOINT")
        if not endpoint.endswith("/layout-detection"):
            endpoint += "/layout-detection"
        body = {
            "file": base64.b64encode(png).decode("ascii"),
            "fileType": 1,
            "threshold": (
                {"6": self.config.ppdoclayout_title_threshold, "17": self.config.ppdoclayout_title_threshold}
                if self.config.ppdoclayout_default_threshold == 0.5
                else {
                    str(index): (
                        self.config.ppdoclayout_title_threshold
                        if index in (6, 17)
                        else self.config.ppdoclayout_default_threshold
                    )
                    for index in range(25)
                }
            ),
            "layoutNms": self.config.ppdoclayout_nms,
            "dynamicBatch": False,
        }
        try:
            assert self._limit is not None
            with self._limit:
                if self.client is None:
                    with httpx.Client(timeout=self.config.ppdoclayout_timeout) as client:
                        response = client.post(endpoint, json=body)
                else:
                    response = self.client.post(endpoint, json=body)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DocumentExtractionError(f"PP-DocLayoutV3 请求失败: {exc}") from exc
        if payload.get("errorCode") != 0 or not isinstance(payload.get("result"), dict):
            raise DocumentExtractionError(f"PP-DocLayoutV3 响应异常: {payload.get('errorMsg')}")
        result = payload["result"]
        if result.get("model") != "PP-DocLayoutV3" or result.get("batchSize", 1) != 1:
            raise DocumentExtractionError("PP-DocLayoutV3 返回了非 batch_size=1 的结果")
        boxes = result.get("boxes")
        if not isinstance(boxes, list):
            raise DocumentExtractionError("PP-DocLayoutV3 缺少版面框")
        return boxes
