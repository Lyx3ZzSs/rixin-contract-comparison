from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
import json
from pathlib import Path

import pytest

from app.config import Settings
from app.infrastructure.artifact_store import LocalArtifactStore
from app.models import CompareTask


class RecordingGenerator:
    def __init__(
        self,
        barrier: threading.Barrier | None = None,
        *,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.barrier = barrier
        self.entered = entered
        self.release = release
        self.calls: list[tuple[int, Path]] = []
        self._lock = threading.Lock()
        self.failure: Exception | None = None

    def generate(self, task: CompareTask, output_path: str | Path) -> Path:
        path = Path(output_path)
        with self._lock:
            self.calls.append((task.report_revision, path))
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(timeout=5)
        if self.failure is not None:
            raise self.failure
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        path.write_bytes(f"%PDF-revision-{task.report_revision}".encode())
        return path


def _report_store_types():
    module = import_module("app.infrastructure.report_store")
    return module.ReportLockRegistry, module.ReportStore


def _store(tmp_path: Path, generator: RecordingGenerator):
    registry_type, store_type = _report_store_types()
    artifacts = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    registry = registry_type()
    return store_type(artifact_store=artifacts, generator=generator, lock_registry=registry), registry


def test_report_artifact_paths_include_report_revision(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))

    assert artifacts.report_pdf_path("T001", 12).name == "contract_compare_report-r12.pdf"
    assert artifacts.report_pdf_path("T001", 13) != artifacts.report_pdf_path("T001", 12)


def test_same_task_revision_concurrency_generates_once_and_returns_one_path(tmp_path: Path) -> None:
    generator_entered = threading.Event()
    release_generator = threading.Event()
    generator = RecordingGenerator(entered=generator_entered, release=release_generator)
    store, registry = _store(tmp_path, generator)
    task = CompareTask(task_id="TSAME", status="COMPLETED", report_revision=7)
    start = threading.Barrier(12)

    def generate() -> Path:
        start.wait(timeout=5)
        return store.ensure_report(task)

    pool = ThreadPoolExecutor(max_workers=12)
    futures = [pool.submit(generate) for _index in range(12)]
    try:
        assert generator_entered.wait(timeout=5)
        assert registry.wait_for_ref_count(task.task_id, task.report_revision, 12, timeout=5)
        assert len(generator.calls) == 1
        final_path = store.artifact_store.report_pdf_path(task.task_id, task.report_revision)
        assert not final_path.exists()
        release_generator.set()
        paths = [future.result(timeout=5) for future in futures]
    finally:
        release_generator.set()
        pool.shutdown(wait=True)

    assert len(generator.calls) == 1
    assert len(set(paths)) == 1
    assert paths[0].name == "contract_compare_report-r7.pdf"
    assert paths[0].read_bytes() == b"%PDF-revision-7"
    assert registry.key_count == 0


def test_different_revisions_use_independent_locks_and_generate_in_parallel(tmp_path: Path) -> None:
    generator = RecordingGenerator(barrier=threading.Barrier(2))
    store, registry = _store(tmp_path, generator)
    tasks = [
        CompareTask(task_id="TPARALLEL", status="COMPLETED", report_revision=3),
        CompareTask(task_id="TPARALLEL", status="COMPLETED", report_revision=4),
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(store.ensure_report, tasks))

    assert {revision for revision, _path in generator.calls} == {3, 4}
    assert {path.name for path in paths} == {
        "contract_compare_report-r3.pdf",
        "contract_compare_report-r4.pdf",
    }
    assert registry.key_count == 0


def test_report_lock_registry_counts_waiters_and_removes_key_after_exceptions() -> None:
    registry_type, _store_type = _report_store_types()
    registry = registry_type()
    holder_entered = threading.Event()
    release_holder = threading.Event()

    def holder() -> None:
        with registry.acquire("TABA", 2):
            holder_entered.set()
            release_holder.wait(timeout=5)

    def waiter() -> None:
        holder_entered.wait(timeout=5)
        with registry.acquire("TABA", 2):
            raise RuntimeError("expected waiter failure")

    with ThreadPoolExecutor(max_workers=2) as pool:
        holding = pool.submit(holder)
        waiting = pool.submit(waiter)
        assert holder_entered.wait(timeout=5)
        assert registry.wait_for_ref_count("TABA", 2, 2, timeout=5)
        release_holder.set()
        holding.result(timeout=5)
        try:
            waiting.result(timeout=5)
        except RuntimeError as exc:
            assert str(exc) == "expected waiter failure"
        else:
            raise AssertionError("waiter did not raise")

    assert registry.key_count == 0


def test_final_report_without_manifest_is_reused_and_manifest_is_self_healed(tmp_path: Path) -> None:
    generator = RecordingGenerator()
    store, _registry = _store(tmp_path, generator)
    task = CompareTask(task_id="THEALMISSING", status="COMPLETED", report_revision=5)
    final_path = store.artifact_store.report_pdf_path(task.task_id, task.report_revision)
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"%PDF-already-committed")

    assert store.ensure_report(task) == final_path

    manifest_path = store.artifact_store.report_manifest_path(task.task_id, task.report_revision)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert generator.calls == []
    assert manifest["task_id"] == task.task_id
    assert manifest["report_revision"] == 5
    assert manifest["report_path"] == final_path.name
    assert manifest["size"] == final_path.stat().st_size


def test_stale_manifest_with_valid_final_is_repaired_without_regeneration(tmp_path: Path) -> None:
    generator = RecordingGenerator()
    store, _registry = _store(tmp_path, generator)
    task = CompareTask(task_id="THEALSTALE", status="COMPLETED", report_revision=6)
    final_path = store.artifact_store.report_pdf_path(task.task_id, task.report_revision)
    manifest_path = store.artifact_store.report_manifest_path(task.task_id, task.report_revision)
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"%PDF-valid")
    manifest_path.write_text('{"task_id":"other","report_revision":99}', encoding="utf-8")

    assert store.ensure_report(task) == final_path

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert generator.calls == []
    assert manifest["task_id"] == "THEALSTALE"
    assert manifest["report_revision"] == 6
    assert manifest["report_path"] == final_path.name


@pytest.mark.parametrize("invalid_final", ["missing", "empty"])
def test_valid_manifest_with_missing_or_empty_final_regenerates(tmp_path: Path, invalid_final: str) -> None:
    first_generator = RecordingGenerator()
    store, registry = _store(tmp_path, first_generator)
    task = CompareTask(task_id=f"TREGEN{invalid_final}", status="COMPLETED", report_revision=8)
    final_path = store.ensure_report(task)
    manifest_path = store.artifact_store.report_manifest_path(task.task_id, task.report_revision)
    if invalid_final == "missing":
        final_path.unlink()
    else:
        final_path.write_bytes(b"")
    replacement_generator = RecordingGenerator()
    _, store_type = _report_store_types()
    replacement_store = store_type(
        artifact_store=store.artifact_store,
        generator=replacement_generator,
        lock_registry=registry,
    )

    assert replacement_store.ensure_report(task).read_bytes() == b"%PDF-revision-8"
    assert len(replacement_generator.calls) == 1
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["size"] == final_path.stat().st_size


@pytest.mark.parametrize(
    "failure_stage",
    ["generation", "flush", "file_fsync", "replace", "parent_fsync", "manifest"],
)
def test_publish_failure_preserves_prior_revision_and_cleans_unique_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    module = import_module("app.infrastructure.report_store")
    generator = RecordingGenerator()
    store, _registry = _store(tmp_path, generator)
    prior_task = CompareTask(task_id="TFAILMATRIX", status="COMPLETED", report_revision=1)
    prior_path = store.ensure_report(prior_task)
    prior_manifest_path = store.artifact_store.report_manifest_path(prior_task.task_id, 1)
    prior_report = prior_path.read_bytes()
    prior_manifest = prior_manifest_path.read_bytes()

    if failure_stage == "generation":
        generator.failure = OSError("generation failed")
    elif failure_stage == "manifest":
        monkeypatch.setattr(
            module, "atomic_write_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("manifest failed"))
        )
    else:
        function_name = {
            "flush": "_flush_file",
            "file_fsync": "_fsync_file",
            "replace": "_replace_file",
            "parent_fsync": "_fsync_directory",
        }[failure_stage]
        monkeypatch.setattr(
            module,
            function_name,
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(f"{failure_stage} failed")),
        )

    next_task = prior_task.model_copy(update={"report_revision": 2})
    with pytest.raises(OSError, match="failed"):
        store.ensure_report(next_task)

    assert prior_path.read_bytes() == prior_report
    assert prior_manifest_path.read_bytes() == prior_manifest
    assert list(prior_path.parent.glob(".contract_compare_report-r2.pdf.*.tmp")) == []


def test_manifest_failure_keeps_published_report_for_next_call_to_self_heal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = import_module("app.infrastructure.report_store")
    generator = RecordingGenerator()
    store, _registry = _store(tmp_path, generator)
    task = CompareTask(task_id="TMANIFESTRETRY", status="COMPLETED", report_revision=4)
    real_atomic_write_json = module.atomic_write_json
    monkeypatch.setattr(
        module, "atomic_write_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("manifest failed"))
    )

    with pytest.raises(OSError, match="manifest failed"):
        store.ensure_report(task)

    final_path = store.artifact_store.report_pdf_path(task.task_id, task.report_revision)
    assert final_path.read_bytes() == b"%PDF-revision-4"
    monkeypatch.setattr(module, "atomic_write_json", real_atomic_write_json)

    assert store.ensure_report(task) == final_path
    assert len(generator.calls) == 1
    assert store.artifact_store.report_manifest_path(task.task_id, 4).is_file()


def test_generation_primary_survives_temp_cleanup_failure_and_registry_is_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = RecordingGenerator()
    generator.failure = RuntimeError("distinct generation primary")
    store, registry = _store(tmp_path, generator)
    task = CompareTask(task_id="TCLEANUPPRIMARY", status="COMPLETED", report_revision=9)
    reports_dir = store.artifact_store.report_pdf_path(task.task_id, task.report_revision).parent
    real_unlink = Path.unlink

    def fail_only_report_temp(path: Path, *args, **kwargs) -> None:
        if path.parent == reports_dir and path.name.startswith(".contract_compare_report-r9.pdf."):
            raise OSError("temp cleanup failed")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_only_report_temp)

    with pytest.raises(RuntimeError, match="distinct generation primary"):
        store.ensure_report(task)

    assert len(generator.calls) == 1
    temp_path = generator.calls[0][1]
    assert temp_path.is_file()
    assert registry.key_count == 0
    monkeypatch.setattr(Path, "unlink", real_unlink)
    temp_path.unlink()
    assert not temp_path.exists()
