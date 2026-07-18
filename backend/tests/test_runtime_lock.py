from __future__ import annotations

from pathlib import Path
import importlib.util

import pytest


def test_api_runtime_lock_excludes_a_second_runtime_and_releases_on_shutdown(tmp_path: Path) -> None:
    spec = importlib.util.find_spec("app.infrastructure.runtime_lock")
    assert spec is not None, "runtime_lock module must provide process-wide API exclusion"
    runtime_lock = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runtime_lock)

    ApiRuntimeLock = runtime_lock.ApiRuntimeLock
    RuntimeLockError = runtime_lock.RuntimeLockError
    first = ApiRuntimeLock(tmp_path)
    second = ApiRuntimeLock(tmp_path)

    first.acquire()
    assert (tmp_path / "runtime" / "api-singleton.lock").exists()
    with pytest.raises(RuntimeLockError, match="MULTI_API_PROCESS_UNSUPPORTED"):
        second.acquire()

    first.release()
    second.acquire()
    second.release()


def test_reentrant_lifespan_acquisition_keeps_the_process_lock_until_all_shutdowns(tmp_path: Path) -> None:
    spec = importlib.util.find_spec("app.infrastructure.runtime_lock")
    assert spec is not None
    runtime_lock = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runtime_lock)
    first = runtime_lock.ApiRuntimeLock(tmp_path)
    second = runtime_lock.ApiRuntimeLock(tmp_path)

    first.acquire()
    first.acquire()
    first.release()
    with pytest.raises(runtime_lock.RuntimeLockError, match="MULTI_API_PROCESS_UNSUPPORTED"):
        second.acquire()
    first.release()
    second.acquire()
    second.release()


def test_missing_fcntl_is_reported_as_an_explicit_unsupported_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure import runtime_lock

    monkeypatch.setattr(runtime_lock, "fcntl", None)

    with pytest.raises(runtime_lock.RuntimeLockError, match="MULTI_API_PROCESS_UNSUPPORTED"):
        runtime_lock.ApiRuntimeLock(tmp_path).acquire()
