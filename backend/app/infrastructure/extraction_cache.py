from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter

from app.services.extractors.base import ExtractionResult

logger = logging.getLogger(__name__)
_EXTRACTION_RESULT_ADAPTER = TypeAdapter(ExtractionResult)


class ExtractionCache(Protocol):
    def get(self, cache_key: str) -> ExtractionResult | None: ...
    def put(self, cache_key: str, result: ExtractionResult) -> None: ...
    def invalidate(self, cache_key: str) -> None: ...


class FileExtractionCache:
    """File-based extraction result cache stored under storage/cache/."""

    def __init__(self, cache_dir: Path, default_ttl_hours: int = 72) -> None:
        self.cache_dir = cache_dir
        self.default_ttl_hours = default_ttl_hours

    def get(self, cache_key: str) -> ExtractionResult | None:
        data_path = self._data_path(cache_key)
        meta_path = self._meta_path(cache_key)
        if not data_path.exists() or not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if self._is_expired(meta):
            self._remove(cache_key)
            return None
        try:
            return _EXTRACTION_RESULT_ADAPTER.validate_json(data_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to deserialize cached extraction result for key %s", cache_key)
            self._remove(cache_key)
            return None

    def put(self, cache_key: str, result: ExtractionResult) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        meta = {"created_at": datetime.now(UTC).isoformat(), "ttl_hours": self.default_ttl_hours}
        # Atomic write: write to temp file then rename
        for path, content in [
            (self._meta_path(cache_key), json.dumps(meta)),
            (self._data_path(cache_key), _EXTRACTION_RESULT_ADAPTER.dump_json(result).decode("utf-8")),
        ]:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, path)

    def invalidate(self, cache_key: str) -> None:
        self._remove(cache_key)

    def _data_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _meta_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.meta.json"

    def _is_expired(self, meta: dict) -> bool:
        created = meta.get("created_at", "")
        ttl = meta.get("ttl_hours", self.default_ttl_hours)
        if not created:
            return True
        try:
            created_dt = datetime.fromisoformat(created)
            elapsed = (datetime.now(UTC) - created_dt).total_seconds() / 3600
            return elapsed > ttl
        except (ValueError, TypeError):
            return True

    def _remove(self, cache_key: str) -> None:
        for path in [self._data_path(cache_key), self._meta_path(cache_key)]:
            if path.exists():
                path.unlink(missing_ok=True)


def compute_cache_key(pdf_path: Path, extractor_name: str, config_fingerprint: str) -> str:
    """Deterministic cache key from file hash + extractor name + config fingerprint."""
    file_hash = _hash_file(pdf_path)
    combined = f"{file_hash}:{extractor_name}:{config_fingerprint}"
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


def _hash_file(path: Path) -> str:
    """SHA-256 hash of first 1MB of file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        chunk = f.read(1 << 20)
        h.update(chunk)
    return h.hexdigest()


class CachedExtractor:
    """Wraps a DocumentExtractor with file-based caching."""

    def __init__(
        self,
        wrapped,
        cache: FileExtractionCache,
        config_fingerprint: str,
    ) -> None:
        self._wrapped = wrapped
        self._cache = cache
        self._fingerprint = config_fingerprint
        self.name = getattr(wrapped, "name", "unknown")

    def extract(self, path, task_id=None):
        cache_key = compute_cache_key(Path(path), self._wrapped.name, self._fingerprint)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.info("Cache hit for %s (key=%s)", path, cache_key[:8])
            return cached
        result = self._wrapped.extract(path, task_id=task_id)
        self._cache.put(cache_key, result)
        return result
