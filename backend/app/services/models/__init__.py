"""Shared model interfaces and seal detection."""
from app.services.layout_analysis import LayoutRegion, LayoutResult
from app.services.models.seal_detector import SealDetector, SealRegion
__all__ = ["LayoutRegion", "LayoutResult", "SealDetector", "SealRegion"]
