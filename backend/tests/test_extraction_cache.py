from __future__ import annotations

from pathlib import Path

from app.infrastructure.extraction_cache import FileExtractionCache
from app.models import Document
from app.services.extractors.base import ExtractionResult


def test_file_extraction_cache_creates_directory_on_write(tmp_path: Path) -> None:
    cache_dir = tmp_path / "storage" / "cache"
    cache = FileExtractionCache(cache_dir)

    assert not cache_dir.exists()

    cache.put(
        "cache-key",
        ExtractionResult(
            document=Document(filename="contract.pdf", path="contract.pdf", page_count=0, pages=[]),
            extractor_used="pymupdf",
        ),
    )

    assert cache_dir.exists()
    assert (cache_dir / "cache-key.json").exists()
    assert (cache_dir / "cache-key.meta.json").exists()
