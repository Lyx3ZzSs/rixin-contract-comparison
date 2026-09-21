"""Alternating baseline/candidate, separate warm API processes, original E2E probe."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/analysis/perf-phase2-20260921/animation"
parser = argparse.ArgumentParser()
parser.add_argument("--space", type=int, required=True)
parser.add_argument("cases", nargs="*", default=list("ABCDE"))
args = parser.parse_args()
assert not os.getenv("DIAG_OPENCV4_SHAPE"), "Diagnostic shape adapters are forbidden"

def stop(process):
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

previews = []
try:
    for variant, port in [("baseline", 4179), ("candidate", 4180)]:
        output = OUT / variant
        output.mkdir(parents=True, exist_ok=True)
        log = (output / "preview.log").open("w")
        previews.append(subprocess.Popen([str(ROOT / "frontend/node_modules/.bin/vite"), "preview", "--host", "127.0.0.1", "--port", str(port), "--strictPort", "--outDir", str(OUT.parent / ("frontend-" + variant))], cwd=ROOT / "frontend", stdout=log, stderr=log))
    for case in args.cases:
        servers = []
        try:
            for variant, port in [("baseline", 8019), ("candidate", 8020)]:
                output = OUT / variant
                env = dict(os.environ, DIAG_OUTPUT=str(output/case), STORAGE_DIR=str(output/("runtime-"+case)), AUTH_MODE="disabled", FRONTEND_CORS_ORIGINS="http://127.0.0.1:4179,http://127.0.0.1:4180", PORT=str(port))
                log = (output/(case+"-server.log")).open("w")
                server = subprocess.Popen([str(ROOT/"backend/.venv/bin/python"), str(ROOT/"experiments/e2e_diagnosis/probe.py")],env=env,stdout=log,stderr=log)
                servers.append(server)
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("API exited")
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).close()
                        break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError("API did not start")
            for repeat in range(1, 4):
                for variant, port in [("baseline", 4179), ("candidate", 4180)]:
                    source = (ROOT/"experiments/e2e_diagnosis/browser_matrix.mjs").read_text()
                    source = source.replace("const output = root + '/output/analysis/e2e-20260921';", "const output = " + json.dumps(str(OUT/variant)) + ";")
                    source = source.replace("output+'/cases.json'", "root+'/output/analysis/e2e-20260921/cases.json'")
                    source = source.replace("taskSpace(3)", f"taskSpace({args.space})").replace("process.env.DIAG_CASE", json.dumps(case))
                    source = source.replace("let repeat=1; repeat<=3", f"let repeat={repeat}; repeat<={repeat}")
                    source = source.replace(":4179/", f":{port}/").replace("attempt<30", "attempt<13").replace("attempt===29", "attempt===12")
                    # Add separate DOM/canvas milestones without changing the readiness endpoint.
                    source = source.replace("await p.evaluate(run =>", "await p.evaluate(() => { new MutationObserver(() => { const d=window.__diag; if(!d.marks.firstResultDOM && document.querySelector('.event-card, .event-empty'))d.marks.firstResultDOM=performance.now(); if(!d.marks.visibleCanvasReady && document.querySelectorAll('canvas[data-render-state=ready]').length>=2)d.marks.visibleCanvasReady=performance.now(); if(document.querySelector('.event-completion-animation'))d.animationObserved=true; }).observe(document.documentElement,{subtree:true,childList:true,attributes:true}); });\n  await p.evaluate(run =>")
                    print(f"{case} {variant} repeat {repeat}", flush=True)
                    subprocess.run(["ego-browser", "nodejs"], input=source, text=True, check=True, timeout=620)
        finally:
            for server in servers:
                stop(server)
finally:
    for preview in previews:
        stop(preview)
