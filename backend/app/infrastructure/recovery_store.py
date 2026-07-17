from __future__ import annotations

import json
import hashlib
import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import Settings, settings
from app.infrastructure.atomic_files import atomic_write_json

logger = logging.getLogger(__name__)


class RecoveryAction(BaseModel):
    action: Literal["unlink", "rmdir"]
    path: str
    scope: Literal["attempt", "final_input"] = "attempt"
    owner_token: str = ""


class RecoveryMarker(BaseModel):
    task_id: str
    attempt_id: str
    primary_error: str
    actions: list[RecoveryAction] = Field(default_factory=list)
    attempts: int = 0
    last_error: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class RecoveryMarkerFile(BaseModel):
    schema_version: int = 2
    task_id: str
    entries: list[RecoveryMarker] = Field(default_factory=list)


class RecoveryStore:
    _process_lock = threading.RLock()

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings

    @property
    def recovery_dir(self) -> Path:
        return self.settings.storage_dir / "recovery"

    def marker_path(self, task_id: str) -> Path:
        return self.recovery_dir / f"{self._safe_task_id(task_id)}.json"

    def create_marker(
        self,
        *,
        task_id: str,
        attempt_id: str,
        primary_error: str,
        actions: list[RecoveryAction],
    ) -> RecoveryMarker:
        marker = RecoveryMarker(
            task_id=task_id,
            attempt_id=attempt_id,
            primary_error=primary_error,
            actions=actions,
        )
        self._validate_actions(marker)
        with self._process_lock:
            entries = self._load_marker_entries_unlocked(task_id)
            existing = next((entry for entry in entries if entry.attempt_id == attempt_id), None)
            if existing is not None:
                return existing
            entries.append(marker)
            self._write_marker_entries_unlocked(task_id, entries)
            return marker

    def load_marker(self, task_id: str, attempt_id: str | None = None) -> RecoveryMarker:
        entries = self.load_marker_entries(task_id)
        if attempt_id is None:
            return entries[0]
        marker = next((entry for entry in entries if entry.attempt_id == attempt_id), None)
        if marker is None:
            raise FileNotFoundError(f"恢复标记不存在: {task_id}/{attempt_id}")
        return marker

    def load_marker_entries(self, task_id: str) -> list[RecoveryMarker]:
        with self._process_lock:
            entries = self._load_marker_entries_unlocked(task_id)
            if not entries:
                raise FileNotFoundError(f"恢复标记不存在: {task_id}")
            return [entry.model_copy(deep=True) for entry in entries]

    def list_markers(self) -> list[RecoveryMarker]:
        if not self.recovery_dir.exists():
            return []
        markers: list[RecoveryMarker] = []
        for path in sorted(self.recovery_dir.glob("*.json")):
            try:
                with self._process_lock:
                    markers.extend(self._load_marker_file(path))
            except (OSError, ValueError, json.JSONDecodeError):
                logger.error("Invalid recovery marker ignored: marker=%s", path.name, exc_info=True)
        return markers

    def recover_all(self) -> bool:
        recovered_all = True
        for marker in self.list_markers():
            if not self.recover_marker(marker):
                recovered_all = False
        return recovered_all

    def recover_marker(self, marker: RecoveryMarker) -> bool:
        return self._execute_marker(marker, remove_owned_marker=True)

    def cleanup_attempt(self, marker: RecoveryMarker) -> bool:
        """Execute ephemeral cleanup without deleting or overwriting another attempt's marker."""
        return self._execute_marker(marker, remove_owned_marker=False)

    def _execute_marker(
        self,
        marker: RecoveryMarker,
        *,
        remove_owned_marker: bool,
    ) -> bool:
        current_action: RecoveryAction | None = None
        try:
            self._validate_actions(marker)
            for action in marker.actions:
                current_action = action
                self._execute_action(marker, action)
            if remove_owned_marker:
                self._remove_marker_entry(marker)
            return True
        except Exception as exc:
            marker.attempts += 1
            marker.last_error = str(exc)
            marker.updated_at = datetime.now(UTC).isoformat()
            try:
                self._upsert_marker_entry(marker)
            except Exception as update_exc:
                logger.critical(
                    "Recovery marker update failed: task_id=%s attempt_id=%s action=%s path=%s "
                    "primary_error=%s action_error=%s actions=%s update_error=%s",
                    marker.task_id,
                    marker.attempt_id,
                    current_action.action if current_action else "validation",
                    current_action.path if current_action else "",
                    marker.primary_error,
                    exc,
                    [action.model_dump(mode="json") for action in marker.actions],
                    update_exc,
                    exc_info=True,
                )
            else:
                logger.error(
                    "Recovery action failed: task_id=%s attempt_id=%s action=%s "
                    "path=%s primary_error=%s action_error=%s",
                    marker.task_id,
                    marker.attempt_id,
                    current_action.action if current_action else "validation",
                    current_action.path if current_action else "",
                    marker.primary_error,
                    exc,
                )
            return False

    def _remove_marker_entry(self, marker: RecoveryMarker) -> None:
        with self._process_lock:
            entries = self._load_marker_entries_unlocked(marker.task_id)
            remaining = [entry for entry in entries if entry.attempt_id != marker.attempt_id]
            if len(remaining) == len(entries):
                return
            if remaining:
                self._write_marker_entries_unlocked(marker.task_id, remaining)
            else:
                self.marker_path(marker.task_id).unlink(missing_ok=True)

    def _upsert_marker_entry(self, marker: RecoveryMarker) -> None:
        with self._process_lock:
            entries = self._load_marker_entries_unlocked(marker.task_id)
            for index, entry in enumerate(entries):
                if entry.attempt_id == marker.attempt_id:
                    entries[index] = marker
                    break
            else:
                entries.append(marker)
            self._write_marker_entries_unlocked(marker.task_id, entries)

    def _execute_action(self, marker: RecoveryMarker, action: RecoveryAction) -> None:
        path = self._validated_action_path(marker, action)
        if action.action == "unlink":
            if action.scope == "final_input" and (path.exists() or path.is_symlink()):
                actual_token = self.ownership_token(path, marker.attempt_id)
                if actual_token != action.owner_token:
                    raise RuntimeError(f"Final input ownership mismatch: {path}")
            path.unlink(missing_ok=True)
            return
        try:
            path.rmdir()
        except FileNotFoundError:
            return

    def _validate_actions(self, marker: RecoveryMarker) -> None:
        for action in marker.actions:
            self._validated_action_path(marker, action)

    def _validated_action_path(self, marker: RecoveryMarker, action: RecoveryAction) -> Path:
        path = Path(action.path)
        if not path.is_absolute():
            path = self.settings.storage_dir / path
        resolved = path.resolve()
        task_root = (self.settings.tasks_dir / self._safe_task_id(marker.task_id)).resolve()
        if task_root not in [resolved, *resolved.parents]:
            raise ValueError(f"恢复路径不属于任务范围: {action.path}")

        attempt_root = (task_root / "staging" / self._safe_path_part(marker.attempt_id)).resolve()
        if action.scope == "attempt":
            if action.action == "rmdir":
                if resolved != attempt_root:
                    raise ValueError(f"恢复路径不属于当前提交尝试: {action.path}")
            elif attempt_root not in [resolved, *resolved.parents] or resolved == attempt_root:
                raise ValueError(f"恢复路径不属于当前提交尝试: {action.path}")
            return resolved

        uploads_root = (task_root / "uploads").resolve()
        if (
            action.action != "unlink"
            or resolved.parent != uploads_root
            or not resolved.name.startswith(("original_", "compare_"))
            or not action.owner_token
        ):
            raise ValueError(f"恢复路径不是当前任务的最终输入: {action.path}")
        return resolved

    def ownership_token(self, path: Path, attempt_id: str) -> str:
        stat = path.stat()
        digest = hashlib.sha256()
        identity = (attempt_id, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        digest.update(":".join(str(value) for value in identity).encode("utf-8"))
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _load_marker_entries_unlocked(self, task_id: str) -> list[RecoveryMarker]:
        path = self.marker_path(task_id)
        if not path.exists():
            return []
        return self._load_marker_file(path)

    def _load_marker_file(self, path: Path) -> list[RecoveryMarker]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "entries" in payload:
            marker_file = RecoveryMarkerFile.model_validate(payload)
            return marker_file.entries
        return [RecoveryMarker.model_validate(payload)]

    def _write_marker_entries_unlocked(self, task_id: str, entries: list[RecoveryMarker]) -> None:
        marker_file = RecoveryMarkerFile(task_id=task_id, entries=entries)
        atomic_write_json(self.marker_path(task_id), marker_file.model_dump(mode="json"))

    @staticmethod
    def _safe_task_id(task_id: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", task_id or "")
        return "task" if sanitized in {"", ".", ".."} else sanitized

    @staticmethod
    def _safe_path_part(value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value or "")
        return "artifact" if sanitized in {"", ".", ".."} else sanitized


default_recovery_store = RecoveryStore()
