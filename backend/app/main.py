from __future__ import annotations

import asyncio
import sys
import os
from contextlib import asynccontextmanager
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as compare_router
from app.clients import close_clients
from app.config import settings
from app.infrastructure.task_repository import default_task_repository
from app.infrastructure.task_runner import default_task_runner
from app.logging_config import setup_logging
from app.services.models.setup import register_default_models, teardown_models

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_storage()
    default_task_repository.resolve()
    default_task_runner.start()
    register_default_models()

    from app.services.progress_bus import ProgressBus
    ProgressBus.get_instance().bind_loop(asyncio.get_running_loop())

    try:
        yield
    finally:
        teardown_models()
        default_task_runner.stop(wait=True)
        close_clients()


app = FastAPI(title="国能日新 · 合同智能审查平台", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_CORS_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(compare_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8002")),
        reload=os.getenv("RELOAD", "true").lower() in {"1", "true", "yes", "on"},
    )
