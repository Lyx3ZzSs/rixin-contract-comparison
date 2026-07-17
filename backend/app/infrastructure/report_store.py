from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.infrastructure.atomic_files import atomic_publish_file, atomic_write_json
from app.models import CompareTask
from app.services.report_generator import ReportGenerator


class ReportGeneratorProtocol(Protocol):
    def generate(self, task: CompareTask, output_path: str | Path) -> Path:
        raise NotImplementedError


@dataclass
class _LockEntry:
    lock: threading.Lock
    ref_count: int = 0


class ReportLockRegistry:
    """Process-local report locks; cross-process exclusion is intentionally unsupported."""

    def __init__(self) -> None:
        self._guard = threading.Condition()
        self._entries: dict[tuple[str, int], _LockEntry] = {}

    @contextmanager
    def acquire(self, task_id: str, report_revision: int) -> Iterator[None]:
        key = (task_id, report_revision)
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = _LockEntry(lock=threading.Lock())
                self._entries[key] = entry
            # Count before blocking so the holder cannot remove an entry that
            # an already-arrived waiter still references.
            entry.ref_count += 1
            self._guard.notify_all()

        acquired = False
        try:
            entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            with self._guard:
                entry.ref_count -= 1
                if entry.ref_count == 0 and self._entries.get(key) is entry:
                    del self._entries[key]
                self._guard.notify_all()

    @property
    def key_count(self) -> int:
        with self._guard:
            return len(self._entries)

    def ref_count(self, task_id: str, report_revision: int) -> int:
        with self._guard:
            entry = self._entries.get((task_id, report_revision))
            return entry.ref_count if entry is not None else 0

    def wait_for_ref_count(
        self,
        task_id: str,
        report_revision: int,
        expected: int,
        *,
        timeout: float,
    ) -> bool:
        with self._guard:
            return self._guard.wait_for(
                lambda: self.ref_count(task_id, report_revision) == expected,
                timeout=timeout,
            )


default_report_lock_registry = ReportLockRegistry()


class ReportStore:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore = default_artifact_store,
        generator: ReportGeneratorProtocol | None = None,
        lock_registry: ReportLockRegistry = default_report_lock_registry,
    ) -> None:
        self.artifact_store = artifact_store
        self.generator = generator or ReportGenerator()
        self.lock_registry = lock_registry

    def ensure_report(self, task: CompareTask) -> Path:
        final_path = self.artifact_store.report_pdf_path(task.task_id, task.report_revision)
        manifest_path = self.artifact_store.report_manifest_path(task.task_id, task.report_revision)
        with self.lock_registry.acquire(task.task_id, task.report_revision):
            if _is_valid_report(final_path):
                manifest = _report_manifest(task, final_path)
                if not _manifest_matches(manifest_path, manifest):
                    atomic_write_json(manifest_path, manifest)
                return final_path
            return self._generate(task, final_path, manifest_path)

    def _generate(self, task: CompareTask, final_path: Path, manifest_path: Path) -> Path:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{final_path.name}.",
            suffix=".tmp",
            dir=final_path.parent,
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            self.generator.generate(task, temp_path)
            if not _is_valid_report(temp_path):
                raise ValueError("报告生成器未生成有效的非空普通文件。")
            atomic_publish_file(temp_path, final_path)
            atomic_write_json(manifest_path, _report_manifest(task, final_path))
            return final_path
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _is_valid_report(path: Path) -> bool:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0


def _report_manifest(task: CompareTask, final_path: Path) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "report_revision": task.report_revision,
        "report_path": final_path.name,
        "size": final_path.stat(follow_symlinks=False).st_size,
    }


def _manifest_matches(path: Path, expected: dict[str, object]) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, UnicodeError):
        return False
    return isinstance(payload, dict) and all(payload.get(key) == value for key, value in expected.items())


default_report_store = ReportStore()
