from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as compare_router
from app.application.compare_tasks import requires_diagnostic_retention
from app.auth.errors import IdentityProviderUnavailable
from app.auth.runtime import AuthRuntime
from app.clients import close_clients
from app.config import settings
from app.infrastructure.artifact_store import default_artifact_store
from app.infrastructure.task_repository import default_task_repository
from app.infrastructure.task_runner import default_task_runner
from app.logging_config import setup_logging
from app.services.models.setup import register_default_models, teardown_models

setup_logging()
logger = logging.getLogger(__name__)
auth_runtime = AuthRuntime(settings.auth)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        settings.ensure_storage()
        default_task_repository.resolve()
        interrupted = default_task_repository.fail_interrupted_tasks()
        if interrupted:
            logger.warning("Marked %s interrupted tasks as failed", interrupted)
        _cleanup_task_files()
        default_task_runner.start()
        register_default_models()

        if settings.auth.is_oidc:
            try:
                auth_runtime.prewarm()
            except IdentityProviderUnavailable:
                logger.warning(
                    "OIDC signing keys are unavailable; authenticated requests will return 503 until recovery"
                )
        else:
            logger.warning(
                "Authentication is disabled; all requests use the fixed local identity sub=%s",
                settings.auth.disabled_user_sub,
            )

        from app.services.progress_bus import ProgressBus

        ProgressBus.get_instance().bind_loop(asyncio.get_running_loop())

        yield
    finally:
        teardown_models()
        default_task_runner.stop(wait=True)
        close_clients()
        auth_runtime.close()
        default_task_repository.close()


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


@app.get("/health")
def health() -> dict[str, str]:
    default_task_repository.healthcheck()
    return {"status": "ok"}


def _cleanup_task_files() -> None:
    diagnostics_cutoff = datetime.now(UTC) - timedelta(days=7)
    for task in default_task_repository.list_compare_tasks():
        default_artifact_store.remove_expired_staging(task.task_id)
        if not requires_diagnostic_retention(task):
            default_artifact_store.remove_diagnostics(task.task_id)
            continue
        try:
            updated_at = datetime.fromisoformat(task.updated_at)
        except ValueError:
            continue
        if updated_at < diagnostics_cutoff:
            default_artifact_store.remove_diagnostics(task.task_id)
