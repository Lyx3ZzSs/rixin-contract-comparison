from __future__ import annotations

import threading
from typing import Protocol

import httpx

from app.config import settings

_ocr_client: httpx.Client | None = None
_lock = threading.Lock()


class HttpClientProvider(Protocol):
    def get_ocr_client(self) -> httpx.Client:
        raise NotImplementedError


class DefaultHttpClientProvider:
    def get_ocr_client(self) -> httpx.Client:
        return get_ocr_client()


def get_ocr_client() -> httpx.Client:
    global _ocr_client
    if _ocr_client is None:
        with _lock:
            if _ocr_client is None:
                _ocr_client = httpx.Client(timeout=settings.ppocrv5_timeout_seconds)
    return _ocr_client


def close_clients() -> None:
    global _ocr_client
    with _lock:
        if _ocr_client is not None:
            _ocr_client.close()
            _ocr_client = None


default_http_client_provider = DefaultHttpClientProvider()
