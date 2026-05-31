from __future__ import annotations

import logging
from typing import Callable

from app.config import settings
from app.config_models import PPStructureSettings, RegistrySettings
from app.services.models.base import ModelProtocol
from app.services.models.registry import ModelRegistry

logger = logging.getLogger(__name__)


def register_default_models(
    extraction_ppstructure: PPStructureSettings | None = None,
    registry_config: RegistrySettings | None = None,
) -> None:
    """Register all default models into the global ModelRegistry.

    Called during app startup.  Models are lazily instantiated on first
    ``get_model()`` call — this only registers the factory functions.

    Args:
        extraction_ppstructure: PP-Structure config. Falls back to
            ``settings.extraction.ppstructure`` if not provided.
        registry_config: Registry config. Falls back to
            ``settings.registry`` if not provided.
    """
    cfg = extraction_ppstructure or settings.extraction.ppstructure
    reg = registry_config or settings.registry
    registry = ModelRegistry.get_instance()

    registry.register("layout_detector", _layout_detector_factory(cfg))
    registry.register("table_recognizer", _table_recognizer_factory())
    registry.register("seal_detector", _seal_detector_factory())

    logger.info(
        "Registered default models: %s",
        registry.list_registered(),
    )

    if reg.preload_models:
        registry.preload_registered(reg.preload_models)


def _layout_detector_factory(cfg: PPStructureSettings) -> Callable[..., ModelProtocol]:
    def factory(**kwargs):
        from app.services.models.layout_detector import LayoutDetector

        return LayoutDetector(
            base_url=cfg.url,
            access_token=cfg.access_token,
            timeout=cfg.timeout_seconds,
            use_table_recognition=cfg.use_table_recognition,
            use_seal_recognition=cfg.use_seal_recognition,
            use_region_detection=cfg.use_region_detection,
            format_block_content=cfg.format_block_content,
            use_doc_orientation_classify=cfg.use_doc_orientation_classify,
            use_doc_unwarping=cfg.use_doc_unwarping,
            use_textline_orientation=cfg.use_textline_orientation,
        )
    return factory


def _table_recognizer_factory() -> Callable[..., ModelProtocol]:
    def factory(**kwargs):
        from app.services.models.table_recognizer import TableRecognizer
        return TableRecognizer()
    return factory


def _seal_detector_factory() -> Callable[..., ModelProtocol]:
    def factory(**kwargs):
        from app.services.models.seal_detector import SealDetector
        return SealDetector()
    return factory


def teardown_models() -> None:
    """Unload all models on app shutdown."""
    registry = ModelRegistry.get_instance()
    registry.clear_all()
    logger.info("Models torn down")
