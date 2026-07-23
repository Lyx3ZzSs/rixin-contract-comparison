from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def _run_api_module():
    module_path = Path(__file__).parents[1] / "scripts" / "run_api.py"
    assert module_path.exists(), "run_api.py is the required API startup wrapper"
    spec = importlib.util.spec_from_file_location("run_api_for_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, None),
        ({"API_WORKERS": "1"}, None),
        ({"WEB_CONCURRENCY": "1"}, None),
        ({"UVICORN_WORKERS": "1"}, None),
        ({"API_WORKERS": "many"}, "MULTI_API_PROCESS_UNSUPPORTED"),
        ({"WEB_CONCURRENCY": "0"}, "MULTI_API_PROCESS_UNSUPPORTED"),
        ({"UVICORN_WORKERS": "2"}, "MULTI_API_PROCESS_UNSUPPORTED"),
        ({"API_WORKERS": "1", "WEB_CONCURRENCY": "2"}, "MULTI_API_PROCESS_UNSUPPORTED"),
    ],
)
def test_validate_single_worker_environment(environment: dict[str, str], expected: str | None) -> None:
    run_api = _run_api_module()
    if expected is None:
        run_api.validate_single_worker_environment(environment)
    else:
        with pytest.raises(RuntimeError, match=expected):
            run_api.validate_single_worker_environment(environment)


def test_invalid_worker_settings_exit_before_uvicorn_is_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    run_api = _run_api_module()
    monkeypatch.setenv("API_WORKERS", "2")
    monkeypatch.delitem(sys.modules, "uvicorn", raising=False)

    with pytest.raises(SystemExit, match="MULTI_API_PROCESS_UNSUPPORTED"):
        run_api.main()

    assert "uvicorn" not in sys.modules


def test_main_starts_exactly_one_uvicorn_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    run_api = _run_api_module()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    fake_uvicorn = SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.delenv("API_WORKERS", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")

    run_api.main()

    assert calls == [(("app.main:app",), {"host": "0.0.0.0", "port": 9000, "workers": 1, "reload": False})]


def test_main_adds_backend_root_before_starting_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    run_api = _run_api_module()
    backend_root = str(Path(run_api.__file__).resolve().parents[1])
    observed_paths: list[list[str]] = []
    fake_uvicorn = SimpleNamespace(run=lambda *_args, **_kwargs: observed_paths.append(list(sys.path)))
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != backend_root])
    monkeypatch.delenv("API_WORKERS", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)

    run_api.main()

    assert observed_paths
    assert backend_root in observed_paths[0]


def test_main_configures_local_quality_paths_when_not_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_api = _run_api_module()
    observed_environment: list[dict[str, str]] = []
    fake_uvicorn = SimpleNamespace(
        run=lambda *_args, **_kwargs: observed_environment.append(dict(run_api.os.environ))
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    for name in (
        "QUALITY_CASES_DIR",
        "QUALITY_RUNS_DIR",
        "QUALITY_CASES_SEED_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    run_api.main()

    repository_root = Path(run_api.__file__).resolve().parents[2]
    assert observed_environment[0]["QUALITY_CASES_DIR"] == str(
        repository_root / "storage" / "quality" / "cases"
    )
    assert observed_environment[0]["QUALITY_RUNS_DIR"] == str(
        repository_root / "storage" / "quality" / "runs"
    )
    assert observed_environment[0]["QUALITY_CASES_SEED_DIR"] == str(
        repository_root / "backend" / "resources" / "quality_cases"
    )


def test_main_preserves_explicit_quality_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    run_api = _run_api_module()
    observed_environment: list[dict[str, str]] = []
    fake_uvicorn = SimpleNamespace(
        run=lambda *_args, **_kwargs: observed_environment.append(dict(run_api.os.environ))
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    explicit = {
        "QUALITY_CASES_DIR": "/custom/cases",
        "QUALITY_RUNS_DIR": "/custom/runs",
        "QUALITY_CASES_SEED_DIR": "/custom/seeds",
    }
    for name, value in explicit.items():
        monkeypatch.setenv(name, value)

    run_api.main()

    for name, value in explicit.items():
        assert observed_environment[0][name] == value


def test_top_level_docs_only_document_the_supported_run_api_startup() -> None:
    repository_root = Path(__file__).parents[2]
    for name in ("README.md", "CLAUDE.md"):
        document = (repository_root / name).read_text(encoding="utf-8")
        assert "python scripts/run_api.py" in document
        assert "python -m uvicorn" not in document
        assert "python app/main.py" not in document
