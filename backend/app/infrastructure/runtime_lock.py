"""Cross-process exclusion for the single FastAPI runtime."""

from __future__ import annotations

import errno
import fcntl
import threading
from pathlib import Path
from typing import TextIO


MULTI_API_PROCESS_UNSUPPORTED = "MULTI_API_PROCESS_UNSUPPORTED"


class RuntimeLockError(RuntimeError):
    """Raised when another API runtime already owns the storage lock."""


class ApiRuntimeLock:
    """Hold an advisory storage lock for one running API process."""

    def __init__(self, storage_dir: Path) -> None:
        self.path = Path(storage_dir) / "runtime" / "api-singleton.lock"
        self._stream: TextIO | None = None
        self._acquisition_count = 0
        self._guard = threading.RLock()

    def acquire(self) -> None:
        with self._guard:
            if self._stream is not None:
                self._acquisition_count += 1
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = self.path.open("a+", encoding="utf-8")
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                stream.close()
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise RuntimeLockError(
                        f"{MULTI_API_PROCESS_UNSUPPORTED}: another API runtime owns {self.path.name}"
                    ) from None
                raise
            self._stream = stream
            self._acquisition_count = 1

    def release(self) -> None:
        with self._guard:
            if self._stream is None:
                return
            self._acquisition_count -= 1
            if self._acquisition_count > 0:
                return
            stream, self._stream = self._stream, None
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()
