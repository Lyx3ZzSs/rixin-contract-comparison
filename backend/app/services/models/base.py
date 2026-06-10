from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ModelProtocol(Protocol):
    """Unified interface for all models (local and remote).

    Inspired by MinerU's model abstraction layer where every model
    (layout detector, OCR, table recognizer, etc.) implements a
    consistent predict / batch_predict / unload contract.
    """

    name: str
    device: str  # "cuda" | "cpu" | "mps"

    def predict(self, input: Any) -> Any:
        """Run inference on a single input."""
        ...

    def batch_predict(self, inputs: list[Any], batch_size: int = 8) -> list[Any]:
        """Run inference on a batch of inputs.

        Default implementation falls back to sequential predict() calls.
        Subclasses with GPU support should override for true batching
        (as MinerU does with resolution/language-based grouping).
        """
        return [self.predict(item) for item in inputs]

    def unload(self) -> None:
        """Release model resources (GPU memory, network connections, etc.).

        Inspired by MinerU's clean_vram pattern for managing VRAM budget
        across multiple concurrently loaded models.
        """
        ...
