from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as compare_router
from app.api_quality import router as quality_router
from app.auth.errors import IdentityProviderUnavailable
from app.auth.runtime import AuthRuntime
from app.application.submission_recovery import SubmissionRecoveryService
from app.clients import close_clients
from app.config import settings
from app.infrastructure.reconciliation import reconcile_startup
from app.infrastructure.recovery_store import default_recovery_store
from app.infrastructure.task_repository import default_task_repository
from app.infrastructure.task_runner import default_task_runner
from app.logging_config import setup_logging
from app.services.models.setup import register_default_models, teardown_models

setup_logging()
logger = logging.getLogger(__name__)
auth_runtime = AuthRuntime(settings.auth)
submission_recovery_service = SubmissionRecoveryService(
    recovery_store=default_recovery_store,
    repository=default_task_repository,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_storage()
    default_task_repository.resolve()
    if not submission_recovery_service.recover_all():
        logger.error("Some pending submission compensation actions remain after startup recovery")
    reconcile_startup(
        default_task_repository,
        default_task_runner.coordinator,
        recovery_store=default_recovery_store,
    )
    default_task_runner.start()
    register_default_models()

    try:
        auth_runtime.prewarm()
    except IdentityProviderUnavailable:
        logger.warning("OIDC signing keys are unavailable; authenticated requests will return 503 until recovery")

    from app.services.progress_bus import ProgressBus

    ProgressBus.get_instance().bind_loop(asyncio.get_running_loop())

    try:
        yield
    finally:
        teardown_models()
        default_task_runner.stop(wait=True)
        close_clients()
        auth_runtime.close()


app = FastAPI(title="国能日新 · 合同智能审查平台", version="0.1.0", lifespan=lifespan)
app.state.auth_runtime = auth_runtime
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_CORS_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:5177,http://localhost:5177",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(compare_router)
app.include_router(quality_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "true").lower() in {"1", "true", "yes", "on"},
    )
