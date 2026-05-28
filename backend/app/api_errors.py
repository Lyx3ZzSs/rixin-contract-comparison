from __future__ import annotations

from fastapi import HTTPException

from app.errors import AppError


def http_error(exc: Exception, *, fallback_status: int = 500, fallback_prefix: str = "") -> HTTPException:
    if isinstance(exc, AppError):
        return HTTPException(status_code=exc.status_code, detail=str(exc))
    detail = f"{fallback_prefix}: {exc}" if fallback_prefix else str(exc)
    return HTTPException(status_code=fallback_status, detail=detail)
