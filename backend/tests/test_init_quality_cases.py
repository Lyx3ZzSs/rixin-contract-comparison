from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from app.config import Settings
from app.services import quality_workbench


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_seed(
    seed_root: Path,
    case_ids: tuple[str, ...] = ("baseline-contract-001",),
    *,
    version: str = "2026-07-17.1",
) -> None:
    _write_json(
        seed_root / "manifest.json",
        {
            "schema_version": "1.0",
            "seed_version": version,
            "cases": [{"case_id": case_id} for case_id in case_ids],
        },
    )
    for case_id in case_ids:
        case_dir = seed_root / case_id
        _write_json(
            case_dir / "expected.json",
            {
                "schema_version": "1.1",
                "case_id": case_id,
                "dataset_split": "regression",
                "case_tags": ["sanitized", "approved"],
                "baseline_required": True,
                "expected_diffs": [],
            },
        )
        _write_json(
            case_dir / "actual.json",
            {
                "task_id": f"seed-{case_id}",
                "status": "COMPLETED",
                "diffs": [],
            },
        )
        tests_dir = case_dir / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "README.md").write_text("Sanitized approved regression case.\n", encoding="utf-8")


def _initialize(target_root: Path, seed_root: Path) -> dict:
    return quality_workbench.initialize_quality_cases(target_root, seed_root)


def test_quality_path_defaults_are_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "QUALITY_CASES_DIR",
        "QUALITY_RUNS_DIR",
        "QUALITY_CASES_SEED_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    app_settings = Settings(_env_file=None)

    assert app_settings.quality_cases_dir == Path("/data/storage/quality/cases")
    assert app_settings.quality_runs_dir == Path("/data/storage/quality/runs")
    assert app_settings.quality_cases_seed_dir == Path("/app/resources/quality_cases")


def test_quality_path_overrides_expand_and_resolve(tmp_path: Path) -> None:
    app_settings = Settings(
        _env_file=None,
        quality_cases_dir=tmp_path / "data/../data/storage/quality/cases",
        quality_runs_dir=tmp_path / "data/storage/quality/runs",
        quality_cases_seed_dir=tmp_path / "app/resources/quality_cases",
    )

    assert app_settings.quality_cases_dir == (tmp_path / "data/storage/quality/cases").resolve()
    assert app_settings.quality_runs_dir == (tmp_path / "data/storage/quality/runs").resolve()
    assert app_settings.quality_cases_seed_dir == (tmp_path / "app/resources/quality_cases").resolve()


def test_initializes_empty_production_like_volume(tmp_path: Path) -> None:
    seed_root = tmp_path / "app" / "resources" / "quality_cases"
    target_root = tmp_path / "data" / "storage" / "quality" / "cases"
    _make_seed(seed_root)

    result = _initialize(target_root, seed_root)

    assert result == {
        "seed_version": "2026-07-17.1",
        "created_case_ids": ["baseline-contract-001"],
        "preserved_case_ids": [],
    }
    source_case = seed_root / "baseline-contract-001"
    target_case = target_root / "baseline-contract-001"
    for source_path in sorted(path for path in source_case.rglob("*") if path.is_file()):
        relative = source_path.relative_to(source_case)
        assert (target_case / relative).read_bytes() == source_path.read_bytes()
    assert json.loads((target_root / "seed-manifest.json").read_text(encoding="utf-8")) == {
        "schema_version": "1.0",
        "seed_version": "2026-07-17.1",
        "case_ids": ["baseline-contract-001"],
    }


def test_second_initialization_is_idempotent(tmp_path: Path) -> None:
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    _make_seed(seed_root)

    first = _initialize(target_root, seed_root)
    expected_path = target_root / "baseline-contract-001" / "expected.json"
    first_stat = expected_path.stat()
    second = _initialize(target_root, seed_root)

    assert first["created_case_ids"] == ["baseline-contract-001"]
    assert second["created_case_ids"] == []
    assert second["preserved_case_ids"] == ["baseline-contract-001"]
    assert expected_path.stat().st_mtime_ns == first_stat.st_mtime_ns


def test_initialization_preserves_administrator_owned_same_id_byte_for_byte(
    tmp_path: Path,
) -> None:
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    _make_seed(seed_root)
    admin_path = target_root / "baseline-contract-001" / "expected.json"
    admin_bytes = b'{"case_id":"baseline-contract-001","owner":"administrator"}\n'
    admin_path.parent.mkdir(parents=True)
    admin_path.write_bytes(admin_bytes)
    fixed_timestamp = 1_700_000_000_000_000_000
    os.utime(admin_path, ns=(fixed_timestamp, fixed_timestamp))

    result = _initialize(target_root, seed_root)

    assert result["created_case_ids"] == []
    assert result["preserved_case_ids"] == ["baseline-contract-001"]
    assert admin_path.read_bytes() == admin_bytes
    assert admin_path.stat().st_mtime_ns == fixed_timestamp


def test_invalid_expected_json_prevents_all_seed_publication(tmp_path: Path) -> None:
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    _make_seed(seed_root, ("valid-case", "invalid-case"))
    (seed_root / "invalid-case" / "expected.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(quality_workbench.QualityCaseInvalidError) as captured:
        _initialize(target_root, seed_root)

    assert captured.value.error_code == "QUALITY_CASE_INVALID"
    assert not (target_root / "valid-case").exists()
    assert not list(target_root.glob(".*.staging")) if target_root.exists() else True


def test_invalid_seed_manifest_schema_prevents_publication(tmp_path: Path) -> None:
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    _make_seed(seed_root)
    manifest_path = seed_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "unsupported"
    _write_json(manifest_path, manifest)

    with pytest.raises(quality_workbench.QualityCaseInvalidError) as captured:
        _initialize(target_root, seed_root)

    assert captured.value.error_code == "QUALITY_CASE_INVALID"
    assert not (target_root / "baseline-contract-001").exists()


def test_unsafe_seed_manifest_case_id_is_reported_as_invalid_seed(
    tmp_path: Path,
) -> None:
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    _make_seed(seed_root)
    _write_json(
        seed_root / "manifest.json",
        {
            "schema_version": "1.0",
            "seed_version": "2026-07-17.1",
            "cases": [{"case_id": "../escape"}],
        },
    )

    with pytest.raises(quality_workbench.QualityCaseInvalidError) as captured:
        _initialize(target_root, seed_root)

    assert captured.value.error_code == "QUALITY_CASE_INVALID"
    assert not target_root.exists()


def test_seed_case_missing_tests_directory_is_rejected(tmp_path: Path) -> None:
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    _make_seed(seed_root)
    (seed_root / "baseline-contract-001" / "tests" / "README.md").unlink()
    (seed_root / "baseline-contract-001" / "tests").rmdir()

    with pytest.raises(quality_workbench.QualityCaseInvalidError) as captured:
        _initialize(target_root, seed_root)

    assert captured.value.error_code == "QUALITY_CASE_INVALID"
    assert "tests" in str(captured.value)
    assert not (target_root / "baseline-contract-001").exists()


@pytest.mark.parametrize("relationship", ["equal", "target_parent", "seed_parent"])
def test_equal_or_parent_child_quality_paths_are_rejected(
    tmp_path: Path,
    relationship: str,
) -> None:
    root = tmp_path / "quality"
    if relationship == "equal":
        target_root = seed_root = root
    elif relationship == "target_parent":
        target_root, seed_root = root, root / "seed"
    else:
        seed_root, target_root = root, root / "cases"

    with pytest.raises(quality_workbench.QualityCasesPathConflictError) as captured:
        quality_workbench.ensure_quality_paths_separated(target_root, seed_root)

    assert captured.value.error_code == "QUALITY_CASES_PATH_CONFLICT"


def test_symlink_resolved_quality_path_conflict_is_rejected(tmp_path: Path) -> None:
    seed_root = tmp_path / "seed"
    seed_root.mkdir()
    target_link = tmp_path / "target-link"
    target_link.symlink_to(seed_root, target_is_directory=True)

    with pytest.raises(quality_workbench.QualityCasesPathConflictError) as captured:
        quality_workbench.ensure_quality_paths_separated(target_link, seed_root)

    assert captured.value.error_code == "QUALITY_CASES_PATH_CONFLICT"


def test_symlink_resolved_ancestor_quality_path_conflict_is_rejected(
    tmp_path: Path,
) -> None:
    resolved_target = tmp_path / "resolved-target"
    seed_root = resolved_target / "seed"
    seed_root.mkdir(parents=True)
    target_link = tmp_path / "target-link"
    target_link.symlink_to(resolved_target, target_is_directory=True)

    with pytest.raises(quality_workbench.QualityCasesPathConflictError) as captured:
        quality_workbench.ensure_quality_paths_separated(target_link, seed_root)

    assert captured.value.error_code == "QUALITY_CASES_PATH_CONFLICT"


def test_service_construction_and_export_recheck_path_conflicts(tmp_path: Path) -> None:
    seed_root = tmp_path / "seed"
    target_root = tmp_path / "cases"
    seed_root.mkdir()
    service = quality_workbench.QualityWorkbenchService(
        case_root=target_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / "runs",
        seed_root=seed_root,
    )
    target_root.symlink_to(seed_root, target_is_directory=True)

    with pytest.raises(quality_workbench.QualityCasesPathConflictError):
        service.export_case("task-001", "case-001")


def test_export_appends_to_target_and_later_initialization_preserves_it(
    tmp_path: Path,
) -> None:
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    task_root = tmp_path / "data/storage/tasks"
    _make_seed(seed_root)
    _write_json(
        task_root / "task-admin" / "task.json",
        {
            "task_id": "task-admin",
            "status": "COMPLETED",
            "original_filename": "approved-original.pdf",
            "compare_filename": "approved-compare.pdf",
            "diffs": [],
        },
    )
    service = quality_workbench.QualityWorkbenchService(
        case_root=target_root,
        task_root=task_root,
        output_root=tmp_path / "data/storage/quality/runs",
        seed_root=seed_root,
    )

    service.export_case("task-admin", "administrator-case")
    expected_path = target_root / "administrator-case" / "expected.json"
    exported_bytes = expected_path.read_bytes()
    exported_mtime = expected_path.stat().st_mtime_ns
    result = _initialize(target_root, seed_root)

    assert result["created_case_ids"] == ["baseline-contract-001"]
    assert expected_path.read_bytes() == exported_bytes
    assert expected_path.stat().st_mtime_ns == exported_mtime


def test_failed_copy_cleans_only_its_hidden_staging_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    _make_seed(seed_root)
    real_copytree = quality_workbench.shutil.copytree

    def partial_copy_then_fail(source: Path, target: Path, *args, **kwargs):
        real_copytree(source, target, *args, **kwargs)
        raise OSError("injected partial copy failure")

    monkeypatch.setattr(quality_workbench.shutil, "copytree", partial_copy_then_fail)

    with pytest.raises(OSError, match="injected partial copy failure"):
        _initialize(target_root, seed_root)

    assert not (target_root / "baseline-contract-001").exists()
    assert not [path for path in target_root.iterdir() if path.name.startswith(".")]


def test_concurrent_initializers_publish_once_without_partial_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    _make_seed(seed_root)
    barrier = Barrier(2)
    real_copytree = quality_workbench.shutil.copytree

    def synchronized_copy(source: Path, target: Path, *args, **kwargs):
        result = real_copytree(source, target, *args, **kwargs)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(quality_workbench.shutil, "copytree", synchronized_copy)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: _initialize(target_root, seed_root), range(2)))

    assert sum("baseline-contract-001" in result["created_case_ids"] for result in results) == 1
    assert sum("baseline-contract-001" in result["preserved_case_ids"] for result in results) == 1
    assert (
        json.loads((target_root / "baseline-contract-001" / "expected.json").read_text(encoding="utf-8"))["case_id"]
        == "baseline-contract-001"
    )
    assert not [path for path in target_root.iterdir() if path.name.startswith(".")]


def test_directory_publication_never_replaces_existing_empty_case(
    tmp_path: Path,
) -> None:
    staged_case = tmp_path / "staged-case"
    staged_case.mkdir()
    (staged_case / "expected.json").write_text("{}\n", encoding="utf-8")
    existing_case = tmp_path / "existing-case"
    existing_case.mkdir()

    with pytest.raises(FileExistsError):
        quality_workbench.publish_directory_without_overwrite(
            staged_case,
            existing_case,
        )

    assert existing_case.is_dir()
    assert not list(existing_case.iterdir())
    assert (staged_case / "expected.json").exists()


def test_directory_publication_fails_closed_on_unsupported_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged_case = tmp_path / "staged-case"
    staged_case.mkdir()
    (staged_case / "expected.json").write_text("{}\n", encoding="utf-8")
    existing_case = tmp_path / "existing-case"
    existing_case.mkdir()
    monkeypatch.setattr(quality_workbench.sys, "platform", "unsupported-os")

    with pytest.raises(NotImplementedError):
        quality_workbench.publish_directory_without_overwrite(
            staged_case,
            existing_case,
        )

    assert existing_case.is_dir()
    assert not list(existing_case.iterdir())
    assert (staged_case / "expected.json").exists()


def test_export_and_initializer_race_publishes_one_complete_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = "shared-case"
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    task_root = tmp_path / "data/storage/tasks"
    _make_seed(seed_root, (case_id,))
    _write_json(
        task_root / "task-admin" / "task.json",
        {
            "task_id": "task-admin",
            "status": "COMPLETED",
            "original_filename": "admin-original.pdf",
            "compare_filename": "admin-compare.pdf",
            "diffs": [],
        },
    )
    service = quality_workbench.QualityWorkbenchService(
        case_root=target_root,
        task_root=task_root,
        output_root=tmp_path / "data/storage/quality/runs",
        seed_root=seed_root,
    )
    barrier = Barrier(2)
    real_publish = quality_workbench.publish_directory_without_overwrite

    def synchronized_publish(source: Path, target: Path) -> None:
        barrier.wait(timeout=5)
        real_publish(source, target)

    monkeypatch.setattr(
        quality_workbench,
        "publish_directory_without_overwrite",
        synchronized_publish,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        init_future = executor.submit(_initialize, target_root, seed_root)
        export_future = executor.submit(service.export_case, "task-admin", case_id)
        init_result = init_future.result()
        try:
            export_future.result()
        except FileExistsError:
            pass

    expected = json.loads((target_root / case_id / "expected.json").read_text(encoding="utf-8"))
    assert expected["case_id"] == case_id
    assert expected.get("source_task_id") in {None, "task-admin"}
    assert set(init_result["created_case_ids"] + init_result["preserved_case_ids"]) == {case_id}
    assert not [path for path in target_root.iterdir() if path.name.startswith(".")]


def test_cli_emits_structured_json_and_meaningful_exit_codes(tmp_path: Path) -> None:
    script_path = BACKEND_ROOT / "scripts" / "init_quality_cases.py"
    assert importlib.util.spec_from_file_location("init_quality_cases", script_path)
    seed_root = tmp_path / "app/resources/quality_cases"
    target_root = tmp_path / "data/storage/quality/cases"
    _make_seed(seed_root)

    success = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--cases-dir",
            str(target_root),
            "--seed-dir",
            str(seed_root),
        ],
        cwd=BACKEND_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    conflict = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--cases-dir",
            str(target_root),
            "--seed-dir",
            str(target_root),
        ],
        cwd=BACKEND_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert success.returncode == 0, success.stderr
    assert json.loads(success.stdout)["status"] == "ok"
    assert conflict.returncode == 2
    assert json.loads(conflict.stderr)["code"] == "QUALITY_CASES_PATH_CONFLICT"


def test_container_and_compose_seed_cases_before_api_startup() -> None:
    dockerfile = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "COPY resources/quality_cases/ ./resources/quality_cases/" in dockerfile
    assert "python scripts/init_quality_cases.py" in dockerfile
    assert dockerfile.index("python scripts/init_quality_cases.py") < dockerfile.index("uvicorn")
    assert "QUALITY_CASES_DIR=/data/storage/quality/cases" in compose
    assert "QUALITY_RUNS_DIR=/data/storage/quality/runs" in compose
    assert "QUALITY_CASES_SEED_DIR=/app/resources/quality_cases" in compose
    assert "storage_data:/data/storage" in compose


def test_deployment_docs_explain_seed_manifest_and_safety() -> None:
    deployment = (REPO_ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")

    for required in (
        "QUALITY_CASES_DIR",
        "QUALITY_RUNS_DIR",
        "QUALITY_CASES_SEED_DIR",
        "init_quality_cases.py",
        "seed-manifest.json",
        "QUALITY_CASES_PATH_CONFLICT",
        "幂等",
        "不会覆盖",
    ):
        assert required in deployment
