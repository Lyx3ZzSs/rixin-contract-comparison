from __future__ import annotations

import gc
import logging

logger = logging.getLogger(__name__)


def detect_device() -> str:
    """Auto-detect the best available inference device.

    Priority order (matching MinerU's get_device logic):
    CUDA > MPS (Apple Silicon) > CPU

    Future: extend for NPU, Ascend, etc.
    """
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass

    return "cpu"


def clean_device_memory(device: str, threshold_gb: float = 8.0) -> None:
    """Release device memory when usage exceeds threshold.

    Mirrors MinerU's clean_vram pattern: when VRAM exceeds the budget,
    trigger gc + empty_cache to free space before loading the next model.
    """
    if device == "cpu":
        gc.collect()
        return

    if device == "cuda":
        try:
            import torch

            allocated_gb = torch.cuda.memory_allocated() / (1024**3)
            if allocated_gb > threshold_gb:
                logger.info(
                    "CUDA memory %.1f GB exceeds threshold %.1f GB, cleaning up",
                    allocated_gb,
                    threshold_gb,
                )
                gc.collect()
                torch.cuda.empty_cache()
        except ImportError:
            pass
    elif device == "mps":
        gc.collect()
