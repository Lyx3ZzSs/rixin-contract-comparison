from __future__ import annotations

import threading

import httpx

from app.config import settings

_ocr_client: httpx.Client | None = None
_llm_client: httpx.Client | None = None
_lock = threading.Lock()


def get_ocr_client() -> httpx.Client:
    global _ocr_client
    if _ocr_client is None:
        with _lock:
            if _ocr_client is None:
                _ocr_client = httpx.Client(timeout=settings.ppocrv5_timeout_seconds)
    return _ocr_client


def get_llm_client() -> httpx.Client:
    global _llm_client
    if _llm_client is None:
        with _lock:
            if _llm_client is None:
                _llm_client = httpx.Client(timeout=settings.ai_extraction_timeout_seconds)
    return _llm_client


def close_clients() -> None:
    global _ocr_client, _llm_client
    with _lock:
        if _ocr_client is not None:
            _ocr_client.close()
            _ocr_client = None
        if _llm_client is not None:
            _llm_client.close()
            _llm_client = None
