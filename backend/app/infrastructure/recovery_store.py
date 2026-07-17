from __future__ import annotations

import json
import logging
import re
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


class RecoveryMarker(BaseModel):
    task_id: str
    attempt_id: str
    primary_error: str
    actions: list[RecoveryAction] = Field(default_factory=list)
    attempts: int = 0
    last_error: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class RecoveryStore:
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
        atomic_write_json(self.marker_path(task_id), marker.model_dump(mode="json"))
        return marker

    def load_marker(self, task_id: str) -> RecoveryMarker:
        return RecoveryMarker.model_validate_json(self.marker_path(task_id).read_text(encoding="utf-8"))

    def list_markers(self) -> list[RecoveryMarker]:
        if not self.recovery_dir.exists():
            return []
        markers: list[RecoveryMarker] = []
        for path in sorted(self.recovery_dir.glob("*.json")):
            try:
                markers.append(RecoveryMarker.model_validate_json(path.read_text(encoding="utf-8")))
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
        return self._execute_marker(marker, remove_owned_marker=True, preserve_other_marker=False)

    def cleanup_attempt(self, marker: RecoveryMarker) -> bool:
        """Execute ephemeral cleanup without deleting or overwriting another attempt's marker."""
        return self._execute_marker(marker, remove_owned_marker=False, preserve_other_marker=True)

    def _execute_marker(
        self,
        marker: RecoveryMarker,
        *,
        remove_owned_marker: bool,
        preserve_other_marker: bool,
    ) -> bool:
        current_action: RecoveryAction | None = None
        try:
            self._validate_actions(marker)
            for action in marker.actions:
                current_action = action
                self._execute_action(marker, action)
            if remove_owned_marker:
                self._remove_marker_if_owned(marker)
            return True
        except Exception as exc:
            marker.attempts += 1
            marker.last_error = str(exc)
            marker.updated_at = datetime.now(UTC).isoformat()
            try:
                if preserve_other_marker and self._marker_owned_by_other_attempt(marker):
                    raise FileExistsError(f"恢复标记已属于其他提交尝试: {self.marker_path(marker.task_id)}")
                atomic_write_json(self.marker_path(marker.task_id), marker.model_dump(mode="json"))
            except Exception as update_exc:
                logger.critical(
                    "Recovery marker update failed: task_id=%s primary_error=%s actions=%s update_error=%s",
                    marker.task_id,
                    marker.primary_error,
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

    def _remove_marker_if_owned(self, marker: RecoveryMarker) -> None:
        marker_path = self.marker_path(marker.task_id)
        try:
            persisted = self.load_marker(marker.task_id)
        except FileNotFoundError:
            return
        if persisted.attempt_id != marker.attempt_id:
            logger.warning(
                "Recovery marker ownership changed; preserving newer marker: "
                "task_id=%s recovered_attempt_id=%s persisted_attempt_id=%s",
                marker.task_id,
                marker.attempt_id,
                persisted.attempt_id,
            )
            return
        marker_path.unlink(missing_ok=True)

    def _marker_owned_by_other_attempt(self, marker: RecoveryMarker) -> bool:
        try:
            persisted = self.load_marker(marker.task_id)
        except FileNotFoundError:
            return False
        return persisted.attempt_id != marker.attempt_id

    def _execute_action(self, marker: RecoveryMarker, action: RecoveryAction) -> None:
        path = self._validated_action_path(marker, action)
        if action.action == "unlink":
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
        ):
            raise ValueError(f"恢复路径不是当前任务的最终输入: {action.path}")
        return resolved

    @staticmethod
    def _safe_task_id(task_id: str) -> str:
        return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", task_id or "") or "task"

    @staticmethod
    def _safe_path_part(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value or "") or "artifact"


default_recovery_store = RecoveryStore()
