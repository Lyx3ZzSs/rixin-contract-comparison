from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace

import pytest

from app.services.pipeline_metrics import get_process_memory_mb


def test_get_process_memory_mb_uses_linux_peak_rss(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resource = SimpleNamespace(
        RUSAGE_SELF=0,
        getrusage=lambda _: SimpleNamespace(ru_maxrss=64 * 1024),
    )
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(sys, "platform", "linux")

    assert get_process_memory_mb() == 64.0


def test_get_process_memory_mb_uses_macos_peak_rss(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resource = SimpleNamespace(
        RUSAGE_SELF=0,
        getrusage=lambda _: SimpleNamespace(ru_maxrss=64 * 1024 * 1024),
    )
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(sys, "platform", "darwin")

    assert get_process_memory_mb() == 64.0


def test_get_process_memory_mb_uses_windows_peak_working_set(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_psutil = SimpleNamespace(
        Process=lambda: SimpleNamespace(
            memory_info=lambda: SimpleNamespace(rss=32 * 1024 * 1024, peak_wset=64 * 1024 * 1024)
        )
    )
    monkeypatch.delitem(sys.modules, "resource", raising=False)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(sys, "platform", "win32")

    assert get_process_memory_mb() == 64.0


def test_get_process_memory_mb_falls_back_to_current_rss(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_psutil = SimpleNamespace(
        Process=lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=32 * 1024 * 1024))
    )
    monkeypatch.delitem(sys.modules, "resource", raising=False)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(sys, "platform", "win32")

    assert get_process_memory_mb() == 32.0


def test_get_process_memory_mb_returns_zero_when_metrics_are_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def unavailable_metrics_import(name: str, *args: object, **kwargs: object) -> object:
        if name in {"psutil", "resource"}:
            raise ImportError(f"{name} unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "psutil", raising=False)
    monkeypatch.delitem(sys.modules, "resource", raising=False)
    monkeypatch.setattr(builtins, "__import__", unavailable_metrics_import)

    assert get_process_memory_mb() == 0.0


def test_get_process_memory_mb_falls_back_when_resource_sampling_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingResource:
        RUSAGE_SELF = 0

        @staticmethod
        def getrusage(_: int) -> object:
            raise OSError("sampling failed")

    fake_psutil = SimpleNamespace(
        Process=lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=32 * 1024 * 1024))
    )
    monkeypatch.setitem(sys.modules, "resource", FailingResource)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert get_process_memory_mb() == 32.0


def test_get_process_memory_mb_returns_zero_when_psutil_sampling_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingProcess:
        def memory_info(self) -> object:
            raise OSError("sampling failed")

    fake_psutil = SimpleNamespace(Process=FailingProcess)
    monkeypatch.delitem(sys.modules, "resource", raising=False)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(sys, "platform", "win32")

    assert get_process_memory_mb() == 0.0
