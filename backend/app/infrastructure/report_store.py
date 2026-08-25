from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Protocol

from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.infrastructure.atomic_files import atomic_publish_file
from app.models import CompareTask
from app.services.report_generator import ReportGenerator


class ReportGeneratorProtocol(Protocol):
    def generate(self, task: CompareTask, output_path: str | Path) -> Path: ...


class ReportStore:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore = default_artifact_store,
        generator: ReportGeneratorProtocol | None = None,
        **_: object,
    ) -> None:
        self.artifact_store = artifact_store
        self.generator = generator or ReportGenerator()

    def ensure_report(self, task: CompareTask) -> Path:
        final_path = self.artifact_store.report_pdf_path(task.task_id, task.report_revision)
        if _is_valid_report(final_path):
            return final_path
        final_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{final_path.name}.",
            suffix=".tmp",
            dir=final_path.parent,
        )
        os.close(descriptor)
        temp_path = Path(temp_name)
        try:
            self.generator.generate(task, temp_path)
            if not _is_valid_report(temp_path):
                raise ValueError("报告生成器未生成有效的非空普通文件。")
            atomic_publish_file(temp_path, final_path)
            self._remove_old_revisions(final_path)
            return final_path
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _remove_old_revisions(current: Path) -> None:
        for path in current.parent.glob("report-r*.pdf"):
            if path != current:
                path.unlink(missing_ok=True)


def _is_valid_report(path: Path) -> bool:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0


default_report_store = ReportStore()
