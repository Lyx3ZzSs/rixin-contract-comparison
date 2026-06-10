from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import httpx

from app.services.extractors.base import DocumentExtractionError

logger = logging.getLogger(__name__)


class RemoteModel:
    """Base class for remote API models implementing ModelProtocol.

    Wraps HTTP calls with lazy client creation, auth, timeout, and
    structured error handling.  Subclasses override ``_request_body``
    and ``_parse_response`` to adapt to specific API endpoints.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        access_token: str = "",
        timeout: int = 600,
        device: str = "cpu",
    ) -> None:
        self._name = name
        self._device = device
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._timeout = timeout
        self._client: httpx.Client | None = None

    # -- ModelProtocol properties ---------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def device(self) -> str:
        return self._device

    # -- HTTP helpers ---------------------------------------------------

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send a POST request and return the JSON response."""
        url = f"{self._base_url}{endpoint}"
        headers = self._build_headers()
        try:
            client = self._get_client()
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else str(exc)
            raise DocumentExtractionError(
                f"远端 {self._name} 请求失败 ({url}, HTTP {exc.response.status_code}): {detail}"
            ) from exc
        except httpx.ConnectError as exc:
            raise DocumentExtractionError(
                f"无法连接远端 {self._name} 服务 ({url}): {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise DocumentExtractionError(
                f"远端 {self._name} 请求超时 ({url}): {exc}"
            ) from exc
        except ValueError as exc:
            raise DocumentExtractionError(
                f"远端 {self._name} 返回内容不是 JSON: {exc}"
            ) from exc
        if payload.get("errorCode") not in (0, None):
            raise DocumentExtractionError(
                f"远端 {self._name} 处理失败: {payload.get('errorMsg') or payload}"
            )
        return payload

    @staticmethod
    def _encode_file(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("ascii")

    # -- ModelProtocol --------------------------------------------------

    def predict(self, input: Any) -> Any:
        raise NotImplementedError

    def batch_predict(self, inputs: list[Any], batch_size: int = 8) -> list[Any]:
        return [self.predict(item) for item in inputs]

    def unload(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.info("Closed HTTP client for model '%s'", self._name)
