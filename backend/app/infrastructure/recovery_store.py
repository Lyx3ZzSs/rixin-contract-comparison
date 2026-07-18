from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterator, Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on unsupported platforms
    fcntl = None

from pydantic import BaseModel, Field

from app.config import Settings, settings
from app.infrastructure.atomic_files import atomic_write_json
from app.logging_config import log_event

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


class RecoveryMarkerLockTimeout(RuntimeError):
    def __init__(self, *, task_id: str, lock_path: Path, timeout_seconds: float) -> None:
        super().__init__(
            f"Recovery marker lock timed out: task_id={task_id} lock_path={lock_path} timeout_seconds={timeout_seconds}"
        )
        self.task_id = task_id
        self.lock_path = lock_path
        self.timeout_seconds = timeout_seconds


class RecoveryMarkerDeferred(RuntimeError):
    """Keep a marker for a later recovery attempt without treating it as repaired."""


class RecoveryStore:
    _process_lock = threading.RLock()
    _lock_timeout_seconds = 5.0
    _lock_poll_seconds = 0.05

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self._lock_state = threading.local()

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
        with self._task_marker_lock(task_id):
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
        with self._task_marker_lock(task_id):
            entries = self._load_marker_entries_unlocked(task_id)
            if not entries:
                raise FileNotFoundError(f"恢复标记不存在: {task_id}")
            return [entry.model_copy(deep=True) for entry in entries]

    def list_markers(self) -> list[RecoveryMarker]:
        markers, _deferred = self._list_markers_with_status()
        return markers

    def _list_markers_with_status(self) -> tuple[list[RecoveryMarker], bool]:
        if not self.recovery_dir.exists():
            return [], False
        markers: list[RecoveryMarker] = []
        deferred = False
        for path in sorted(self.recovery_dir.glob("*.json")):
            try:
                with self._task_marker_lock(path.stem):
                    markers.extend(self._load_marker_file(path))
            except RecoveryMarkerLockTimeout as exc:
                deferred = True
                self._log_deferred(exc, marker=path.name)
            except (OSError, ValueError, json.JSONDecodeError):
                logger.error("Invalid recovery marker ignored: marker=%s", path.name, exc_info=True)
        return markers, deferred

    def recover_all(
        self,
        prepare_marker: Callable[[RecoveryMarker], RecoveryMarker | None] | None = None,
    ) -> bool:
        markers, deferred = self._list_markers_with_status()
        recovered_all = not deferred
        for marker in markers:
            try:
                candidate = prepare_marker(marker) if prepare_marker is not None else marker
                if candidate is not None and not self.recover_marker(candidate):
                    recovered_all = False
            except RecoveryMarkerLockTimeout as exc:
                recovered_all = False
                self._log_deferred(exc, marker=self.marker_path(marker.task_id).name)
            except RecoveryMarkerDeferred as exc:
                recovered_all = False
                self._log_deferred(exc, marker=self.marker_path(marker.task_id).name)
        return recovered_all

    @contextmanager
    def journal_submission_attempt(
        self,
        *,
        task_id: str,
        attempt_id: str,
        actions: list[RecoveryAction],
    ) -> Iterator[RecoveryMarker]:
        """Record all staging cleanup before bytes are written for an attempt.

        Holding the task lock through the caller's upload and publish sequence
        prevents a concurrent recovery from consuming an otherwise empty
        staging journal before the first stream creates its destination.
        """
        marker = RecoveryMarker(
            task_id=task_id,
            attempt_id=attempt_id,
            primary_error="submission attempt pending task persistence",
            actions=actions,
        )
        self._validate_actions(marker)
        with self._task_marker_lock(task_id):
            entries = self._load_marker_entries_unlocked(task_id)
            existing = next((entry for entry in entries if entry.attempt_id == attempt_id), None)
            if existing is None:
                entries.append(marker)
                self._write_marker_entries_unlocked(task_id, entries)
            else:
                merged_actions = self._merge_actions(existing.actions, actions)
                if merged_actions != existing.actions:
                    existing.actions = merged_actions
                    existing.updated_at = datetime.now(UTC).isoformat()
                    self._write_marker_entries_unlocked(task_id, entries)
                marker = existing
            yield marker.model_copy(deep=True)

    @contextmanager
    def journal_final_publish(
        self,
        *,
        task_id: str,
        attempt_id: str,
        action: RecoveryAction,
        attempt_actions: list[RecoveryAction],
    ) -> Iterator[RecoveryMarker]:
        """Durably record this submission attempt before publishing a final input.

        The marker lock deliberately spans the irreversible link publication so
        a concurrent recovery cannot consume a pre-publication journal entry.
        """
        marker = RecoveryMarker(
            task_id=task_id,
            attempt_id=attempt_id,
            primary_error="submission attempt pending task persistence",
            actions=[action, *attempt_actions],
        )
        self._validate_actions(marker)
        with self._task_marker_lock(task_id):
            entries = self._load_marker_entries_unlocked(task_id)
            existing = next((entry for entry in entries if entry.attempt_id == attempt_id), None)
            if existing is None:
                entries.append(marker)
                self._write_marker_entries_unlocked(task_id, entries)
            else:
                merged_actions = self._merge_actions(existing.actions, [action, *attempt_actions])
                if merged_actions != existing.actions:
                    existing.actions = merged_actions
                    existing.updated_at = datetime.now(UTC).isoformat()
                    self._write_marker_entries_unlocked(task_id, entries)
                marker = existing
            yield marker.model_copy(deep=True)

    def merge_marker(
        self,
        *,
        task_id: str,
        attempt_id: str,
        primary_error: str,
        actions: list[RecoveryAction],
    ) -> RecoveryMarker:
        """Add this attempt's compensation actions without touching other attempts."""
        self.create_marker(
            task_id=task_id,
            attempt_id=attempt_id,
            primary_error=primary_error,
            actions=actions,
        )
        with self._task_marker_lock(task_id):
            entries = self._load_marker_entries_unlocked(task_id)
            existing = next(entry for entry in entries if entry.attempt_id == attempt_id)
            merged_actions = self._merge_actions(existing.actions, actions)
            if existing.primary_error != primary_error or merged_actions != existing.actions:
                existing.primary_error = primary_error
                existing.actions = merged_actions
                existing.updated_at = datetime.now(UTC).isoformat()
                self._write_marker_entries_unlocked(task_id, entries)
            return existing.model_copy(deep=True)

    def finalize_final_inputs(self, marker: RecoveryMarker) -> RecoveryMarker | None:
        """Remove only final-input recovery actions after the Task commit boundary."""
        return self._finalize_actions(marker, scope="final_input")

    def finalize_attempt_actions(self, marker: RecoveryMarker) -> RecoveryMarker | None:
        """Remove only completed attempt cleanup actions from this marker."""
        return self._finalize_actions(marker, scope="attempt")

    def _finalize_actions(
        self,
        marker: RecoveryMarker,
        *,
        scope: Literal["attempt", "final_input"],
    ) -> RecoveryMarker | None:
        with self._task_marker_lock(marker.task_id):
            entries = self._load_marker_entries_unlocked(marker.task_id)
            current = next((entry for entry in entries if entry.attempt_id == marker.attempt_id), None)
            if current is None:
                return None
            remaining_actions = [action for action in current.actions if action.scope != scope]
            if len(remaining_actions) == len(current.actions):
                return current.model_copy(deep=True)
            if remaining_actions:
                current.actions = remaining_actions
                current.updated_at = datetime.now(UTC).isoformat()
                self._write_marker_entries_unlocked(marker.task_id, entries)
                return current.model_copy(deep=True)
            entries = [entry for entry in entries if entry.attempt_id != marker.attempt_id]
            if entries:
                self._write_marker_entries_unlocked(marker.task_id, entries)
            else:
                self.marker_path(marker.task_id).unlink(missing_ok=True)
            return None

    @staticmethod
    def _merge_actions(
        current: list[RecoveryAction],
        additions: list[RecoveryAction],
    ) -> list[RecoveryAction]:
        merged = list(current)
        known = {
            (action.action, action.path, action.scope, action.owner_token)
            for action in current
        }
        for action in additions:
            identity = (action.action, action.path, action.scope, action.owner_token)
            if identity not in known:
                merged.append(action)
                known.add(identity)
        return merged

    @staticmethod
    def _log_deferred(exc: RecoveryMarkerLockTimeout | RecoveryMarkerDeferred, *, marker: str) -> None:
        log_event(
            logger,
            "recovery_marker_deferred",
            task_id=getattr(exc, "task_id", None),
            recovery_marker=marker,
            error_type=type(exc).__name__,
            error_code="RECOVERY_DEFERRED",
        )
        logger.warning(
            "event=recovery_marker_deferred marker=%s task_id=%s reason=%s detail=%s",
            marker,
            getattr(exc, "task_id", ""),
            "lock_timeout" if isinstance(exc, RecoveryMarkerLockTimeout) else "precondition_unavailable",
            exc,
        )

    def recover_marker(self, marker: RecoveryMarker) -> bool:
        with self._task_marker_lock(marker.task_id):
            return self._execute_marker(marker, remove_owned_marker=True)

    def cleanup_attempt(self, marker: RecoveryMarker) -> bool:
        """Execute ephemeral cleanup without deleting or overwriting another attempt's marker."""
        with self._task_marker_lock(marker.task_id):
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
                log_event(
                    logger,
                    "compensation_failed",
                    task_id=marker.task_id,
                    attempt=marker.attempts,
                    recovery_marker=marker.attempt_id,
                    error_type=type(exc).__name__,
                    error_code="RECOVERY_ACTION_FAILED",
                )
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
        with self._task_marker_lock(marker.task_id):
            entries = self._load_marker_entries_unlocked(marker.task_id)
            remaining = [entry for entry in entries if entry.attempt_id != marker.attempt_id]
            if len(remaining) == len(entries):
                return
            if remaining:
                self._write_marker_entries_unlocked(marker.task_id, remaining)
            else:
                self.marker_path(marker.task_id).unlink(missing_ok=True)

    def _upsert_marker_entry(self, marker: RecoveryMarker) -> None:
        with self._task_marker_lock(marker.task_id):
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
            if action.scope == "final_input":
                self._unlink_owned_final_input(marker, action, path)
            else:
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

    def _unlink_owned_final_input(
        self,
        marker: RecoveryMarker,
        action: RecoveryAction,
        path: Path,
    ) -> None:
        quarantine = self._quarantine_path(marker, path)
        if quarantine.exists() or quarantine.is_symlink():
            self._resolve_final_input_quarantine(marker, action, path, quarantine)
            return
        if not path.exists() and not path.is_symlink():
            return

        os.rename(path, quarantine)
        self._resolve_final_input_quarantine(marker, action, path, quarantine)

    def _resolve_final_input_quarantine(
        self,
        marker: RecoveryMarker,
        action: RecoveryAction,
        path: Path,
        quarantine: Path,
    ) -> None:
        actual_token = self.ownership_token(quarantine, marker.attempt_id)
        if actual_token == action.owner_token:
            quarantine.unlink()
            if path.exists() or path.is_symlink():
                logger.warning(
                    "Final input was replaced during recovery; replacement preserved: task_id=%s attempt_id=%s path=%s",
                    marker.task_id,
                    marker.attempt_id,
                    path,
                )
            return

        try:
            path.hardlink_to(quarantine)
        except FileExistsError as restore_error:
            logger.critical(
                "Final input ownership mismatch and quarantine restore was blocked by a replacement: "
                "task_id=%s attempt_id=%s path=%s quarantine=%s",
                marker.task_id,
                marker.attempt_id,
                path,
                quarantine,
            )
            raise RuntimeError(
                f"Final input ownership mismatch; quarantine retained because replacement exists: {quarantine}"
            ) from restore_error
        quarantine.unlink()
        raise RuntimeError(f"Final input ownership mismatch: {path}")

    def _quarantine_path(self, marker: RecoveryMarker, path: Path) -> Path:
        attempt = self._safe_path_part(marker.attempt_id)
        return path.with_name(f".{path.name}.{attempt}.recovery-quarantine")

    @contextmanager
    def _task_marker_lock(self, task_id: str) -> Iterator[None]:
        with self._process_lock:
            if fcntl is None:
                raise RuntimeError("Recovery marker operations require POSIX fcntl advisory locks")
            lock_dir = self.recovery_dir / ".locks"
            lock_dir.mkdir(parents=True, exist_ok=True)
            lock_path = lock_dir / f"{self._safe_task_id(task_id)}.lock"
            lock_key = str(lock_path.resolve())
            state_pid = getattr(self._lock_state, "pid", None)
            if state_pid != os.getpid():
                self._lock_state.pid = os.getpid()
                self._lock_state.held = {}
            held: dict[str, int] = self._lock_state.held
            if lock_key in held:
                held[lock_key] += 1
                try:
                    yield
                finally:
                    held[lock_key] -= 1
                return

            with lock_path.open("a+b") as lock_file:
                deadline = time.monotonic() + self._lock_timeout_seconds
                while True:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError as lock_error:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise RecoveryMarkerLockTimeout(
                                task_id=task_id,
                                lock_path=lock_path,
                                timeout_seconds=self._lock_timeout_seconds,
                            ) from lock_error
                        time.sleep(min(self._lock_poll_seconds, remaining))
                held[lock_key] = 1
                try:
                    yield
                finally:
                    held.pop(lock_key, None)
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

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
