from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.models import BBox
from app.services.models.layout_detector import LayoutRegion, LayoutResult

logger = logging.getLogger(__name__)


@dataclass
class SealRegion:
    """Detected seal region."""

    bbox: BBox
    page_number: int
    confidence: float = 1.0
    text: str = ""
    raw_data: dict = field(default_factory=dict)


class SealDetector:
    """Extracts seal regions from layout detection results.

    Does NOT make additional API calls — filters seal regions already
    identified by ``LayoutDetector``.
    """

    name = "seal_detector"
    device = "cpu"

    SEAL_LABELS = {"seal", "stamp"}

    def predict(self, input: Any) -> list[SealRegion]:
        """Extract seal regions.

        Args:
            input: A ``LayoutResult`` from LayoutDetector.

        Returns:
            List of ``SealRegion`` s found in the layout.
        """
        if isinstance(input, LayoutResult):
            return self._from_layout(input)
        if isinstance(input, LayoutRegion):
            return self._from_single_region(input)
        raise TypeError(f"SealDetector expects LayoutResult or LayoutRegion, got {type(input)}")

    def batch_predict(self, inputs: list[Any], batch_size: int = 8) -> list[list[SealRegion]]:
        return [self.predict(item) for item in inputs]

    def _from_layout(self, layout: LayoutResult) -> list[SealRegion]:
        return [
            self._to_seal(r)
            for r in layout.regions
            if r.region_type in self.SEAL_LABELS
        ]

    def _from_single_region(self, region: LayoutRegion) -> list[SealRegion]:
        if region.region_type in self.SEAL_LABELS:
            return [self._to_seal(region)]
        return []

    @staticmethod
    def _to_seal(region: LayoutRegion) -> SealRegion:
        return SealRegion(
            bbox=region.bbox,
            page_number=region.page_number,
            confidence=region.confidence,
            text=region.text,
            raw_data=region.raw_data,
        )

    def unload(self) -> None:
        pass
