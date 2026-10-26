"""Shared benchmark I/O; application imports stay lazy for frozen-code runs."""
from __future__ import annotations

import base64
from contextlib import AbstractContextManager
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

import httpx


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def configure_runtime(root: Path, code: Path, output: Path, overrides=None):
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(code))
    import app.config as config

    config.settings = config.Settings(
        _env_file=(root / ".env", root / "backend/.env"),
        base_dir=root, storage_dir=output / "runtime", **(overrides or {}),
    )
    return config.settings


def build_service(runtime, extractor=None):
    from app.infrastructure.artifact_store import ArtifactStore
    from app.infrastructure.task_repository import SQLiteTaskRepository
    from app.services.compare_service import CompareService

    return CompareService(extractor=extractor, structured_extractor=extractor,
                          repository=SQLiteTaskRepository(runtime), artifact_store=ArtifactStore(runtime))


def input_paths(root: Path, task_id: str) -> dict[str, Path]:
    source = root / "storage/tasks" / task_id / "input"
    return {side: next((source / side).glob("*.pdf")) for side in ("original", "compare")}


def code_hashes(code: Path) -> dict[str, str]:
    return {str(path.relative_to(code)): digest(path.read_bytes()) for path in sorted((code / "app").rglob("*.py"))}


def save_task(target: Path, task) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "result.json").write_text(task.model_dump_json(indent=2))


def http_failures(calls: list[dict]) -> int:
    return sum(bool(c.get("error_type")) or c.get("status", 200) >= 400 for c in calls)


class HttpRecorder(AbstractContextManager):
    """Record real calls or replay captured responses; always restore the client.

    Set task_id only between sequential tasks. Concurrent benchmarks record
    aggregate calls without attributing provider threads to individual tasks.
    """

    def __init__(self, *, mode="live", cache: Path | None = None, cache_by_task=False):
        if mode not in {"live", "replay"} or (mode == "replay" and cache is None):
            raise ValueError("HTTP replay requires a response cache")
        if cache_by_task and cache is None:
            raise ValueError("Task-scoped recording requires a response cache")
        self.mode, self.cache, self.cache_by_task = mode, cache, cache_by_task
        self.task_id = ""
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def __enter__(self):
        self._real_send = httpx.Client.send

        def send(client, request, **kwargs):
            return self._send(client, request, **kwargs)

        httpx.Client.send = send
        return self

    def __exit__(self, *exc):
        httpx.Client.send = self._real_send

    def _send(self, client, request, **kwargs):
        record = {"endpoint": request.url.path, "mode": self.mode}
        if self.task_id:
            record["task_id"] = self.task_id
        target = None
        if self.cache is not None:
            body = request.read()
            key = digest(request.method.encode() + b"\0" + str(request.url).encode() + b"\0" + body)
            scope = self.cache / self.task_id if self.cache_by_task else self.cache
            target = scope / (key + ".json")
            record.update(request_sha256=key, request_bytes=len(body))
        start = time.perf_counter()
        try:
            if self.mode == "replay":
                if not target.exists():
                    record["cache_miss"] = True
                    raise httpx.RequestError(f"Missing benchmark HTTP response: {target.stem}", request=request)
                saved = json.loads(target.read_text())
                response = httpx.Response(saved["status"], content=base64.b64decode(saved["body"]),
                                          headers={"content-type": saved["content_type"]}, request=request)
                record["cache_hit"] = True
            else:
                response = self._real_send(client, request, **kwargs)
                response.read()
                if target is not None:
                    # Preserve failures as failures, rather than cache misses.
                    saved = {"status": response.status_code,
                             "content_type": response.headers.get("content-type", "application/json"),
                             "body": base64.b64encode(response.content).decode()}
                    with self._lock:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        temp = target.with_suffix(".tmp")
                        write_json(temp, saved)
                        temp.replace(target)
            record["status"] = response.status_code
            if not response.is_success:
                record["error_response"] = response.text[:2000]
            return response
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["seconds"] = time.perf_counter() - start
            with self._lock:
                self.calls.append(record)
