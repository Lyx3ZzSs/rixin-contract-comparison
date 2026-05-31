from __future__ import annotations

import logging
from typing import Callable, Protocol, runtime_checkable

from app.clients import HttpClientProvider, default_http_client_provider
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.services.extractors.base import DocumentExtractor

logger = logging.getLogger(__name__)


@runtime_checkable
class ExtractorFactory(Protocol):
    def __call__(
        self,
        *,
        artifact_store: ArtifactStore,
        client_provider: HttpClientProvider,
    ) -> DocumentExtractor: ...


class ExtractorRegistry:
    """Named extractor registry replacing factory.py's hardcoded if-else.

    Register extractors with their name and aliases, then build by name.
    Mirrors the pattern from ``ModelRegistry`` but for document extractors.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., DocumentExtractor]] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self,
        name: str,
        aliases: list[str] | None = None,
        factory: Callable[..., DocumentExtractor] | None = None,
    ) -> None:
        """Register an extractor factory by name with optional aliases.

        Args:
            name: Primary name (e.g. ``"pymupdf"``).
            aliases: Alternative names (e.g. ``["fitz", "pdf_text"]``).
            factory: Callable that returns a ``DocumentExtractor``.
        """
        if factory is None:
            logger.debug("No factory provided for extractor '%s', skipping", name)
            return
        self._factories[name] = factory
        for alias in (aliases or []):
            self._aliases[alias] = name

    def build(
        self,
        name: str,
        *,
        artifact_store: ArtifactStore = default_artifact_store,
        client_provider: HttpClientProvider = default_http_client_provider,
    ) -> DocumentExtractor:
        """Build an extractor by name or alias.

        Raises:
            ValueError: If the name is not registered.
        """
        resolved = self._aliases.get(name, name)
        factory = self._factories.get(resolved)
        if factory is None:
            available = sorted(set(list(self._factories.keys()) + list(self._aliases.keys())))
            raise ValueError(f"Unknown extractor '{name}'. Available: {available}")
        return factory(artifact_store=artifact_store, client_provider=client_provider)

    def list_available(self) -> list[str]:
        """Return all registered names and aliases."""
        return sorted(set(list(self._factories.keys()) + list(self._aliases.keys())))


def create_default_registry() -> ExtractorRegistry:
    """Create and populate the default extractor registry."""
    from app.services.extractors.pymupdf import PyMuPDFExtractor
    from app.services.extractors.ppocrv5 import PPOCRV5Extractor
    from app.services.extractors.ppstructure_ocr_hybrid import PPStructureOCRHybridExtractor

    registry = ExtractorRegistry()

    registry.register(
        "pymupdf",
        aliases=["fitz", "pdf_text"],
        factory=lambda **kw: PyMuPDFExtractor(),
    )

    registry.register(
        "ppocrv5",
        aliases=["pp_ocrv5", "paddleocr", "paddle_ocr", "paddle"],
        factory=lambda **kw: PPOCRV5Extractor(
            client_provider=kw["client_provider"],
            artifact_store=kw["artifact_store"],
        ),
    )

    registry.register(
        "ppstructure_ocr_hybrid",
        aliases=["ppstructure_ppocrv5", "structure_ocr", "ppstructure"],
        factory=lambda **kw: PPStructureOCRHybridExtractor(
            client_provider=kw["client_provider"],
            artifact_store=kw["artifact_store"],
        ),
    )

    return registry


default_extractor_registry = create_default_registry()
