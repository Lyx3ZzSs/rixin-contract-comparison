"""Disposable PDF geometry cache. Never updates comparison results or revisions."""
from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
from pydantic import ValidationError

from app.api_schemas import PreviewDocument, PreviewDocuments, PreviewMetadata, PreviewPage
from app.infrastructure.atomic_files import atomic_write_json
from app.models import CompareTask
from app.utils.file_utils import assert_path_inside_storage
from app.infrastructure.artifact_store import default_artifact_store


def preview_metadata(task: CompareTask) -> PreviewMetadata:
    task_root = default_artifact_store.task_root(task.task_id)
    paths = {side: Path(getattr(task, f"{side}_pdf_path")) for side in ("original", "compare")}
    revisions = {}
    for side, path in paths.items():
        assert_path_inside_storage(path)
        if not path.is_file():
            raise FileNotFoundError("PDF unavailable")
        stat = path.stat()
        revisions[side] = hashlib.sha256(
            f"1:{task.task_id}:{side}:{stat.st_size}:{stat.st_mtime_ns}".encode()
        ).hexdigest()
    cache = task_root / "preview" / "metadata-v1.json"
    assert_path_inside_storage(cache)
    try:
        cached = PreviewMetadata.model_validate_json(cache.read_text())
        if all(getattr(cached.documents, side).source_revision == rev for side, rev in revisions.items()):
            return cached
    except (OSError, ValueError, ValidationError):
        pass
    documents = {}
    for side, path in paths.items():
        try:
            with fitz.open(path) as pdf:
                documents[side] = PreviewDocument(
                    source_revision=revisions[side], page_count=len(pdf),
                    pages=[PreviewPage(page_no=i + 1, width_points=p.rect.width,
                                       height_points=p.rect.height, rotation=p.rotation)
                           for i, p in enumerate(pdf)],
                )
        except (RuntimeError, ValueError) as exc:
            raise ValueError("PDF geometry unavailable") from exc
    result = PreviewMetadata(documents=PreviewDocuments(**documents))
    # Geometry is usable even if a read-only deployment cannot persist its cache.
    try:
        atomic_write_json(cache, result.model_dump(mode="json"))
    except OSError:
        pass
    return result
