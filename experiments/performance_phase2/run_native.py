"""Configuration-only pilot; halt each candidate immediately on semantic difference."""
import json
import os
from pathlib import Path
import subprocess
import time

from compare_results import differences

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/analysis/perf-phase2-20260921/native"
OUT.mkdir(parents=True, exist_ok=True)
plan = {"fast": ("enabled", "off"), "page": ("off", "enabled"), "both": ("enabled", "enabled")}
report = {}

class SemanticGateRejected(RuntimeError):
    pass

def run_one(path, case, env, log, *, baseline=None):
    process = subprocess.Popen([str(ROOT/"backend/.venv/bin/python"), str(ROOT/"experiments/performance_phase2/run_pipeline.py"), case, str(path)],env=env,stdout=log,stderr=log)
    deadline = time.monotonic() + 600
    try:
        while process.poll() is None:
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(process.args, 600)
            if baseline is not None:
                try:
                    def dimensions(directory):
                        rows=[json.loads(x) for x in (directory/'spans.jsonl').read_text().splitlines()]
                        return next(x for x in rows if x['kind']=='clause_dimensions')
                    old,new=dimensions(baseline),dimensions(path)
                    if any(old[side]['count']!=new[side]['count'] for side in ['original','compare']):
                        reason='Native extraction changed clause partition; stop before accepting downstream semantics'
                        (path/'early-rejection.json').write_text(json.dumps(dict(reason=reason,
                            baseline={s:old[s] for s in ['original','compare']},
                            candidate={s:new[s] for s in ['original','compare']},pipeline_completed=False),indent=2))
                        raise SemanticGateRejected(reason)
                except (FileNotFoundError, StopIteration, json.JSONDecodeError):
                    pass
            time.sleep(.25)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode,process.args)
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()

for mode, config in plan.items():
    report[mode] = {"config": config, "runs": [], "decision": "testing"}
    stopped = False
    # Native text PDFs are the primary target; fail B before spending more calls.
    for case in "BCDAE":
        for repeat in range(1, 4):
            results = {}
            for variant, flags in [("baseline", ("off", "off")), ("candidate", config)]:
                path = OUT / mode / f"{case}-{repeat}-{variant}"
                path.mkdir(parents=True, exist_ok=False)
                env = dict(os.environ, COMPARE_NATIVE_FAST_PATH_MODE=flags[0], COMPARE_NATIVE_PAGE_MODE=flags[1], PHASE2_CAPTURE_EXTRACTION="1")
                with (path / "run.log").open("w") as log:
                    try:
                        run_one(path,case,env,log,baseline=OUT/mode/f'{case}-{repeat}-baseline' if variant=='candidate' else None)
                    except (subprocess.SubprocessError, OSError, SemanticGateRejected) as exc:
                        report[mode]["decision"] = "REVERT: " + (str(exc) if isinstance(exc,SemanticGateRejected) else type(exc).__name__)
                        stopped = True
                        break
                results[variant] = json.loads((path / "result.json").read_text())
            if stopped:
                break
            a,b = results["baseline"], results["candidate"]
            # Extraction source/blocks and final business output are both retained;
            # a changed extraction hash alone is not a correctness verdict.
            da = {k: a[k] for k in ["diffs", "pairs"]}
            db = {k: b[k] for k in ["diffs", "pairs"]}
            da["page_mapping"] = a["page_alignment"]["entries"]
            db["page_mapping"] = b["page_alignment"]["entries"]
            delta = differences(da,db)
            record = {"case":case,"repeat":repeat,"baseline_seconds":a["pipeline_seconds"],"candidate_seconds":b["pipeline_seconds"],"semantic_difference_paths":delta,"extraction":{v:results[v]["extraction"] for v in results}}
            report[mode]["runs"].append(record)
            print(mode,case,repeat,"semantic differences",len(delta),flush=True)
            if delta:
                report[mode]["decision"] = "REVERT: strict semantic equivalence failed; remaining runs intentionally stopped"
                stopped=True
                break
        if stopped:
            break
    if not stopped:
        report[mode]["decision"] = "component equivalence passed; live Golden and browser E2E still required"
    (OUT/"report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False))
(OUT/"complete.json").write_text(json.dumps({mode: r["decision"] for mode,r in report.items()}))
