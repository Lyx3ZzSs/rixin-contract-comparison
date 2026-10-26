"""Task-owned, byte-bounded cache of immutable PDF renderings."""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from pathlib import Path

import fitz
import numpy as np

from app.services.pipeline_metrics import PerformanceRecorder


class PageRenderCache:
    def __init__(self, max_bytes: int = 64 * 1024**2):
        self.max_bytes = max(0, max_bytes)
        self._images: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._identities: dict[tuple, str] = {}
        self.bytes = 0
        self.peak_bytes = 0
        self.metrics = PerformanceRecorder()

    def render(self, page: fitz.Page, *, scale: float, colorspace=fitz.csRGB,
               alpha: bool = False, annots: bool = True, clip=None) -> np.ndarray:
        # The caller owns the PDF handle. No handle is cached or shared.
        path = Path(page.parent.name) if page.parent.name else None
        identity = None
        if path is not None and path.is_file() and self.max_bytes and not page.parent.is_dirty:
            stat = path.stat()
            signature = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
            if signature not in self._identities:
                self._identities[signature] = hashlib.sha256(path.read_bytes()).hexdigest()
            identity = self._identities[signature]
        key = (identity, page.number, tuple(page.rect), page.rotation, scale, colorspace.name,
               alpha, annots, tuple(clip) if clip is not None else None)
        if identity is not None and key in self._images:
            self.metrics.increment("hits")
            self._images.move_to_end(key)
            return self._images[key]
        self.metrics.increment("misses")
        with self.metrics.measure("render"):
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=colorspace,
                                  alpha=alpha, annots=annots, clip=clip)
            shape = (pix.height, pix.width) if pix.n == 1 else (pix.height, pix.width, pix.n)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(shape)
            image.setflags(write=False)
        if identity is not None and image.nbytes <= self.max_bytes:
            while self._images and self.bytes + image.nbytes > self.max_bytes:
                _, evicted = self._images.popitem(last=False)
                self.bytes -= evicted.nbytes
                self.metrics.increment("evictions")
            self._images[key] = image
            self.bytes += image.nbytes
            self.peak_bytes = max(self.peak_bytes, self.bytes)
        self.metrics.set_counter("retained_bytes", self.bytes)
        self.metrics.set_counter("peak_retained_bytes", self.peak_bytes)
        return image

    def clear(self):
        self._images.clear()
        self._identities.clear()
        self.bytes = 0
