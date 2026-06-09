from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

from app.services.models.layout_detector import LayoutRegion, LayoutResult
from app.services.models.registry import ModelRegistry

logger = logging.getLogger(__name__)

# Maps layout region types to model names in the registry.
REGION_MODEL_MAP: dict[str, str] = {
    "table": "table_recognizer",
    "table_title": "table_recognizer",
    "table_caption": "table_recognizer",
    "seal": "seal_detector",
    "stamp": "seal_detector",
}


class BatchProcessor:
    """Groups layout regions by type and processes them in batches.

    Mirrors MinerU's ``batch_predict`` optimisation: inputs of the same
    type (and similar resolution) are grouped and sent to the model
    together, reducing overhead for models that support true batching.
    """

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        batch_size: int = 8,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self._registry = registry or ModelRegistry.get_instance()
        self._batch_size = batch_size
        self._progress_callback = progress_callback

    def process_regions(self, layout: LayoutResult) -> dict[str, list[Any]]:
        """Process all layout regions by routing to appropriate models.

        Returns:
            Dict mapping model name to list of results.
        """
        groups = self._group_by_model(layout.regions)
        results: dict[str, list[Any]] = {}
        total_regions = len(layout.regions)
        processed = 0

        for model_name, regions in groups.items():
            try:
                model = self._registry.get_model(model_name)
            except KeyError:
                logger.debug("Model '%s' not registered, skipping %d regions", model_name, len(regions))
                continue

            for chunk_start in range(0, len(regions), self._batch_size):
                chunk = regions[chunk_start:chunk_start + self._batch_size]
                batch_result = model.batch_predict(chunk, batch_size=self._batch_size)
                if isinstance(batch_result, list):
                    results.setdefault(model_name, []).extend(batch_result)
                else:
                    results.setdefault(model_name, []).append(batch_result)
                processed += len(chunk)
                if self._progress_callback:
                    self._progress_callback(model_name, processed, total_regions)

        return results

    @staticmethod
    def _group_by_model(regions: list[LayoutRegion]) -> dict[str, list[LayoutRegion]]:
        """Group regions by their target model name."""
        groups: dict[str, list[LayoutRegion]] = defaultdict(list)
        for region in regions:
            model_name = REGION_MODEL_MAP.get(region.region_type)
            if model_name is not None:
                groups[model_name].append(region)
        return dict(groups)
