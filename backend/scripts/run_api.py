"""The only supported production entry point for the FastAPI service."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from collections.abc import Mapping


WORKER_ENVIRONMENT_NAMES = ("API_WORKERS", "WEB_CONCURRENCY", "UVICORN_WORKERS")
MULTI_API_PROCESS_UNSUPPORTED = "MULTI_API_PROCESS_UNSUPPORTED"


def validate_single_worker_environment(environ: Mapping[str, str] | None = None) -> None:
    """Reject process-manager settings that would create a second API runtime."""
    environment = os.environ if environ is None else environ
    for name in WORKER_ENVIRONMENT_NAMES:
        raw_value = environment.get(name)
        if raw_value is None:
            continue
        try:
            worker_count = int(raw_value.strip())
        except (AttributeError, ValueError):
            raise RuntimeError(f"{MULTI_API_PROCESS_UNSUPPORTED}: {name} must be the integer 1") from None
        if worker_count != 1:
            raise RuntimeError(f"{MULTI_API_PROCESS_UNSUPPORTED}: {name} must be 1")


def main() -> None:
    try:
        validate_single_worker_environment()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None

    backend_root = str(Path(__file__).resolve().parents[1])
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    # This import must remain after the worker-settings validation so an
    # invalid deployment cannot initialize an application runtime.
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        workers=1,
        reload=False,
    )


if __name__ == "__main__":
    main()
