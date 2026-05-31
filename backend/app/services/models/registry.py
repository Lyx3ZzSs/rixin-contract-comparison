from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Callable, ClassVar

from app.services.models.base import ModelProtocol

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Thread-safe model registry with lazy loading, LRU eviction, and preload.

    Inspired by MinerU's AtomModelSingleton:
    - Models are registered by name with a factory function.
    - get_model() lazily instantiates and caches models.
    - Already-loaded models are reused across calls (singleton per config).
    - Optional LRU eviction when ``max_loaded`` > 0.
    - Optional preload at startup via ``preload_registered()``.
    """

    _instance: ClassVar[ModelRegistry | None] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, max_loaded: int = 0) -> None:
        self._factories: dict[str, Callable[..., ModelProtocol]] = {}
        self._models: dict[str, ModelProtocol] = {}
        self._access_times: dict[str, float] = {}
        self._lock = threading.RLock()
        self._max_loaded = max_loaded

    @classmethod
    def get_instance(cls) -> ModelRegistry:
        """Global singleton accessor (same pattern as MinerU's AtomModelSingleton)."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # -- Registration --------------------------------------------------

    def register(self, name: str, factory: Callable[..., ModelProtocol]) -> None:
        """Register a model factory by name.

        The factory is called lazily on first get_model() and should
        accept optional **kwargs for device, config overrides, etc.
        """
        with self._lock:
            self._factories[name] = factory

    def register_many(self, mappings: dict[str, Callable[..., ModelProtocol]]) -> None:
        for name, factory in mappings.items():
            self.register(name, factory)

    # -- Access --------------------------------------------------------

    def get_model(self, name: str, **kwargs: Any) -> ModelProtocol:
        """Get or lazily instantiate a model.

        If the model is already loaded, returns the cached instance.
        Otherwise calls the registered factory to create it.

        Raises KeyError if no factory is registered for *name*.
        """
        with self._lock:
            if name in self._models:
                self._access_times[name] = time.monotonic()
                return self._models[name]
            if name not in self._factories:
                raise KeyError(
                    f"No model factory registered for '{name}'. "
                    f"Available: {list(self._factories.keys())}"
                )
            model = self._factories[name](**kwargs)
            self._models[name] = model
            self._access_times[name] = time.monotonic()
            logger.info("Loaded model '%s' (device=%s)", name, model.device)
            self._evict_if_needed()
            return model

    # -- Lifecycle -----------------------------------------------------

    def unload(self, name: str) -> None:
        """Unload a specific model and release its resources."""
        with self._lock:
            model = self._models.pop(name, None)
            self._access_times.pop(name, None)
            if model is not None:
                model.unload()
                logger.info("Unloaded model '%s'", name)

    def clear_all(self) -> None:
        """Unload all models. Mirrors MinerU's VRAM cleanup on pipeline end."""
        with self._lock:
            for name in list(self._models.keys()):
                self._unload_unlocked(name)
            self._models.clear()
            self._access_times.clear()
            logger.info("Cleared all models from registry")

    def list_loaded(self) -> list[str]:
        with self._lock:
            return list(self._models.keys())

    def list_registered(self) -> list[str]:
        with self._lock:
            return list(self._factories.keys())

    # -- Preload -------------------------------------------------------

    def preload_registered(self, names: list[str] | None = None) -> None:
        """Instantiate models eagerly. Called at app startup."""
        target = names if names is not None else list(self._factories.keys())
        for name in target:
            try:
                self.get_model(name)
            except Exception:
                logger.warning("预加载模型 '%s' 失败", name, exc_info=True)

    # -- Memory tracking -----------------------------------------------

    def memory_usage_mb(self) -> dict[str, float]:
        """Return estimated memory usage (MB) per loaded model."""
        usage: dict[str, float] = {}
        for name, model in self._models.items():
            try:
                size = sys.getsizeof(model) / (1024 * 1024)
            except TypeError:
                size = 0.0
            usage[name] = round(size, 2)
        return usage

    # -- Internal ------------------------------------------------------

    def _evict_if_needed(self) -> None:
        """Evict least-recently-used model when over the max_loaded limit."""
        if self._max_loaded <= 0:
            return
        while len(self._models) > self._max_loaded:
            lru_name = min(self._access_times, key=self._access_times.get)
            logger.info("LRU eviction: unloading model '%s'", lru_name)
            self._unload_unlocked(lru_name)
            del self._access_times[lru_name]

    def _unload_unlocked(self, name: str) -> None:
        model = self._models.pop(name, None)
        self._access_times.pop(name, None)
        if model is not None:
            model.unload()
