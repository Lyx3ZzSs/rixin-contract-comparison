"""The only supported production entry point for the FastAPI service."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from collections.abc import Mapping


WORKER_ENVIRONMENT_NAMES = ("API_WORKERS", "WEB_CONCURRENCY", "UVICORN_WORKERS")
MULTI_API_PROCESS_UNSUPPORTED = "MULTI_API_PROCESS_UNSUPPORTED"
QUALITY_PATH_NAMES = (
    "QUALITY_CASES_DIR",
    "QUALITY_RUNS_DIR",
    "QUALITY_CASES_SEED_DIR",
)


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


def configure_local_quality_paths(environ: dict[str, str] | None = None) -> None:
    """Supply repository-local quality paths without overriding explicit config."""
    environment = os.environ if environ is None else environ
    backend_root = Path(__file__).resolve().parents[1]
    repository_root = backend_root.parent
    file_values = _read_selected_env_values(
        (repository_root / ".env", backend_root / ".env"),
        {"STORAGE_DIR", *QUALITY_PATH_NAMES},
    )
    storage_value = environment.get("STORAGE_DIR") or file_values.get("STORAGE_DIR") or "storage"
    storage_root = Path(storage_value).expanduser()
    if not storage_root.is_absolute():
        storage_root = repository_root / storage_root

    defaults = {
        "QUALITY_CASES_DIR": storage_root / "quality" / "cases",
        "QUALITY_RUNS_DIR": storage_root / "quality" / "runs",
        "QUALITY_CASES_SEED_DIR": backend_root / "resources" / "quality_cases",
    }
    for name, path in defaults.items():
        if name not in environment and name not in file_values:
            environment[name] = str(path)


def _read_selected_env_values(
    paths: tuple[Path, ...],
    selected_names: set[str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            if stripped.startswith("export "):
                stripped = stripped.removeprefix("export ").lstrip()
            name, value = stripped.split("=", 1)
            name = name.strip()
            if name in selected_names:
                values[name] = value.strip().strip("\"'")
    return values


def main() -> None:
    try:
        validate_single_worker_environment()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None

    backend_root = str(Path(__file__).resolve().parents[1])
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    configure_local_quality_paths()

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
