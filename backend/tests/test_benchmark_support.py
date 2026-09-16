from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from scripts.benchmark_support import HttpRecorder, http_failures


def test_http_capture_and_replay_keep_task_scopes_and_failure_responses(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200 if len(calls) == 1 else 400, json={"call": len(calls)})

    real_send = httpx.Client.send
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with HttpRecorder(cache=tmp_path, cache_by_task=True) as recorder:
            for task in ("a", "b"):
                recorder.task_id = task
                client.post("https://model.test/rerank", json={"query": "same input"})
        assert httpx.Client.send is real_send
        assert http_failures(recorder.calls) == 1
        with HttpRecorder(mode="replay", cache=tmp_path, cache_by_task=True) as replay:
            for task, status, number in (("a", 200, 1), ("b", 400, 2)):
                replay.task_id = task
                response = client.post("https://model.test/rerank", json={"query": "same input"})
                assert response.status_code == status
                assert response.json() == {"call": number}
        assert len(calls) == 2  # Replay must not contact the transport.
        assert http_failures(replay.calls) == 1
        assert all(row["cache_hit"] for row in replay.calls)


def test_missing_replay_is_an_error_and_restores_client(tmp_path):
    real_send = httpx.Client.send
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected network call"))) as client:
        with pytest.raises(httpx.RequestError, match="Missing benchmark HTTP response"):
            with HttpRecorder(mode="replay", cache=tmp_path) as recorder:
                client.get("https://model.test/missing")
    assert httpx.Client.send is real_send
    assert recorder.calls[0]["cache_miss"]
    assert http_failures(recorder.calls) == 1


def test_aggregate_recorder_keeps_parallel_calls_and_transport_errors():
    def respond(request):
        if request.url.path == "/fail":
            raise httpx.ConnectError("test failure", request=request)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client, HttpRecorder() as recorder:
        with ThreadPoolExecutor(max_workers=3) as executor:
            responses = list(executor.map(client.get, ["https://model.test/ok"] * 6))
        with pytest.raises(httpx.ConnectError):
            client.get("https://model.test/fail")
    assert all(response.status_code == 200 for response in responses)
    assert len(recorder.calls) == 7
    assert http_failures(recorder.calls) == 1


@pytest.mark.parametrize("fail_one", [False, True])
def test_concurrency_driver_persists_both_tasks_and_counts_only_successes(tmp_path, fail_one):
    script = Path(__file__).resolve().parents[1] / "scripts/benchmark_compare_concurrency.py"
    sample = tmp_path / "input.pdf"
    sample.write_bytes(b"queue smoke test; fake comparison does not parse the PDF")
    output = tmp_path / "run"
    # A separate process preserves the driver's import-time Settings isolation.
    code = f'''
import runpy, sys, threading
from pathlib import Path
from types import SimpleNamespace
main = runpy.run_path({str(script)!r})["main"]
barrier = threading.Barrier(2)
def build_service(runtime, extractor=None):
    from app.infrastructure.task_repository import SQLiteTaskRepository
    repository = SQLiteTaskRepository(runtime)
    def compare(original, compare, task_id, execution_context):
        barrier.wait(timeout=5)
        if {fail_one!r} and task_id.endswith("-b"):
            raise RuntimeError("expected smoke-test failure")
        task = repository.load_compare_task(task_id)
        task.status = "COMPLETED"
        return task
    return SimpleNamespace(repository=repository, compare=compare)
main.__globals__["build_service"] = build_service
main.__globals__["input_paths"] = lambda root, task: {{"original": Path({str(sample)!r}), "compare": Path({str(sample)!r})}}
sys.argv = [{str(script)!r}, "--output", {str(output)!r}, "--workers", "2", "--task-id", "a", "--task-id", "b"]
raise SystemExit(main())
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == int(fail_one), result.stderr
    report = json.loads((output / "summary.json").read_text())
    assert len(report["tasks"]) == 2
    assert report["http_calls"] == 0
    assert report["tasks_per_minute"] == pytest.approx((2 - int(fail_one)) * 60 / report["wall_seconds"])
    for task_id in ("a", "b"):
        task = json.loads((output / task_id / "result.json").read_text())
        assert task["status"] == ("FAILED" if fail_one and task_id == "b" else "COMPLETED")
