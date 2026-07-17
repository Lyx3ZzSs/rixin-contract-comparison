from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_manifest_lock = threading.RLock()


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _publish_synced_file(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def atomic_publish_file(source: Path, destination: Path) -> None:
    """Durably publish an already-written file without changing metadata."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    _fsync_file(source)
    _publish_synced_file(source, destination)


def update_task_manifest(
    manifest_path: Path,
    *,
    task_id: str,
    fields: Mapping[str, Any] | None = None,
    artifact: Mapping[str, Any] | None = None,
) -> None:
    """Merge one task metadata/artifact update and publish the manifest atomically."""
    with _manifest_lock:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        except (OSError, ValueError, TypeError, UnicodeError):
            manifest = {}
        if not isinstance(manifest, dict):
            manifest = {}

        artifacts = {
            str(item.get("path")): item
            for item in manifest.get("artifacts", [])
            if isinstance(item, dict) and item.get("path")
        }
        if artifact is not None and artifact.get("path"):
            artifacts[str(artifact["path"])] = dict(artifact)

        manifest.update(fields or {})
        manifest.update(
            {
                "task_id": task_id,
                "updated_at": datetime.now(UTC).isoformat(),
                "artifacts": sorted(artifacts.values(), key=lambda item: str(item["path"])),
            }
        )
        atomic_write_json(manifest_path, manifest)


def _publish_synced_file(source: Path, destination: Path) -> None:
    backup_path = _snapshot_existing_file(destination)
    replaced = False
    try:
        os.replace(source, destination)
        replaced = True
        _fsync_directory(destination.parent)
    except BaseException:
        if replaced:
            _restore_previous_target(destination, backup_path)
        raise
    finally:
        if backup_path is not None:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass


def _snapshot_existing_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".bak", dir=path.parent)
    os.close(fd)
    backup_path = Path(temp_name)
    try:
        backup_path.unlink()
        os.link(path, backup_path)
    except BaseException:
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return backup_path


def _restore_previous_target(destination: Path, backup_path: Path | None) -> None:
    try:
        if backup_path is None:
            destination.unlink(missing_ok=True)
        else:
            os.replace(backup_path, destination)
        _fsync_directory(destination.parent)
    except OSError:
        # The original durability error remains the actionable failure.  The
        # destination is still a complete old or new file, never a partial one.
        pass


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
