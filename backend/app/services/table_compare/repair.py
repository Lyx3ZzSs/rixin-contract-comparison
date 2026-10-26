"""Compatibility facade for layered table repair modules."""

from __future__ import annotations

from app.services.table_compare.business_repair import BusinessRepairMixin
from app.services.table_compare.repair_context import RepairDiagnosticsMixin, TableRepairContext
from app.services.table_compare.structural_repair import StructuralRepairMixin


class TableRepairService(StructuralRepairMixin, BusinessRepairMixin, RepairDiagnosticsMixin):
    """Layered table repair service.

    Structural and business-specific rules live in separate mixins; this class
    keeps the existing import path stable for parser, summary, and tests.
    """

    def __init__(self) -> None:
        super().__init__()


__all__ = ["TableRepairContext", "TableRepairService"]
