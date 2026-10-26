from __future__ import annotations

import os
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TextIO

import pytest

from app.infrastructure import atomic_files


@pytest.mark.parametrize("failure", ["write", "file_fsync", "replace", "parent_fsync"])
def test_atomic_write_failure_preserves_previous_target_and_cleans_temps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    target = tmp_path / "metadata.json"
    target.write_text('{"revision": 1}', encoding="utf-8")
    original = target.read_text(encoding="utf-8")

    if failure == "write":
        fdopen = os.fdopen

        class FailingStream(AbstractContextManager[TextIO]):
            def __init__(self, stream: TextIO) -> None:
                self.stream = stream

            def __enter__(self) -> "FailingStream":
                return self

            def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
                self.stream.close()
                return False

            def write(self, _content: str) -> int:
                raise OSError("write failed")

            def flush(self) -> None:
                self.stream.flush()

            def fileno(self) -> int:
                return self.stream.fileno()

        monkeypatch.setattr(os, "fdopen", lambda *args, **kwargs: FailingStream(fdopen(*args, **kwargs)))
    elif failure == "file_fsync":
        fsync = os.fsync
        calls = 0

        def fail_file_fsync(fd: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("file fsync failed")
            fsync(fd)

        monkeypatch.setattr(os, "fsync", fail_file_fsync)
    elif failure == "replace":
        monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace failed")))
    else:
        fsync = os.fsync
        calls = 0

        def fail_parent_fsync(fd: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("parent fsync failed")
            fsync(fd)

        monkeypatch.setattr(os, "fsync", fail_parent_fsync)

    with pytest.raises(OSError, match="failed"):
        atomic_files.atomic_write_text(target, '{"revision": 2}')

    assert target.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_atomic_publish_file_replaces_destination_without_manifest_side_effect(tmp_path: Path) -> None:
    source = tmp_path / "report.tmp"
    destination = tmp_path / "report.pdf"
    source.write_bytes(b"new report")
    destination.write_bytes(b"old report")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"before": true}', encoding="utf-8")

    atomic_files.atomic_publish_file(source, destination)

    assert destination.read_bytes() == b"new report"
    assert not source.exists()
    assert manifest.read_text(encoding="utf-8") == '{"before": true}'
