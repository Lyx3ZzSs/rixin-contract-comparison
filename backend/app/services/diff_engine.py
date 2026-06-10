"""Backward-compatible import path — delegates to app.services.diff package."""

from app.services.diff import DiffEngine

__all__ = ["DiffEngine"]
