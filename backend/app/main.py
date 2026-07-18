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
from app.infrastructure.runtime_lock import ApiRuntimeLock
from app.infrastructure.task_repository import default_task_repository
from app.infrastructure.task_runner import default_task_runner
from app.logging_config import log_event, setup_logging
from app.services.models.setup import register_default_models, teardown_models
from app.services.quality_workbench import initialize_quality_cases

setup_logging()
logger = logging.getLogger(__name__)
auth_runtime = AuthRuntime(settings.auth)
submission_recovery_service = SubmissionRecoveryService(
    recovery_store=default_recovery_store,
    repository=default_task_repository,
)
runtime_lock = ApiRuntimeLock(settings.storage_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime_lock.acquire()
    try:
        settings.ensure_storage()
        initialize_quality_cases(settings.quality_cases_dir, settings.quality_cases_seed_dir)
        default_task_repository.resolve()
        if not submission_recovery_service.recover_all():
            logger.error("Some pending submission compensation actions remain after startup recovery")
        repaired = reconcile_startup(
            default_task_repository,
            default_task_runner.coordinator,
            recovery_store=default_recovery_store,
        )
        log_event(logger, "startup_reconciled", recovery_marker="startup", duration_ms=repaired)
        default_task_runner.start()
        register_default_models()

        try:
            auth_runtime.prewarm()
        except IdentityProviderUnavailable:
            logger.warning("OIDC signing keys are unavailable; authenticated requests will return 503 until recovery")

        from app.services.progress_bus import ProgressBus

        ProgressBus.get_instance().bind_loop(asyncio.get_running_loop())

        yield
    finally:
        teardown_models()
        default_task_runner.stop(wait=True)
        close_clients()
        auth_runtime.close()
        runtime_lock.release()


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
