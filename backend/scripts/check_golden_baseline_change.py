from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PREFIX = "backend/tests/fixtures/contract_golden/"
ORACLE_PREFIX = f"{FIXTURE_PREFIX}oracle/"
MANIFEST_PATH = f"{FIXTURE_PREFIX}manifest.json"
APPROVAL_ENV = "GOLDEN_BASELINE_CHANGE_APPROVED"
PROTECTED_PATHS = {
    "backend/tests/golden_contract/semantic_matcher.py",
    "backend/tests/golden_contract/test_contract_golden_blocking.py",
    "backend/scripts/check_golden_baseline_change.py",
}
DECISION_KEYS = {
    "assertion",
    "change_decision",
    "quality_status",
    "content_recognition_status",
}


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def _git_names(*args: str) -> set[str]:
    result = _git("diff", "--name-only", *args)
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _untracked_names() -> set[str]:
    result = _git("ls-files", "--others", "--exclude-standard")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _is_protected(path: str) -> bool:
    return path.startswith(FIXTURE_PREFIX) or path in PROTECTED_PATHS


def _working_tree_names() -> set[str]:
    return _git_names() | _git_names("--cached") | _untracked_names()


def _select_base_ref(working_tree_names: set[str]) -> str:
    explicit = os.environ.get("GOLDEN_BASE_REF", "").strip()
    if explicit:
        return explicit
    if any(_is_protected(name) for name in working_tree_names):
        return "HEAD"
    parent = _git("rev-parse", "--verify", "HEAD^", check=False)
    return "HEAD^" if parent.returncode == 0 else "HEAD"


def _changed_names(base_ref: str, working_tree_names: set[str]) -> set[str]:
    names = set(working_tree_names)
    if base_ref != "HEAD":
        names |= _git_names(f"{base_ref}...HEAD")
    return names


def _read_worktree_json(path: str) -> dict[str, Any] | None:
    absolute = REPOSITORY_ROOT / path
    if not absolute.is_file():
        return None
    return json.loads(absolute.read_text(encoding="utf-8"))


def _read_revision_json(ref: str, path: str) -> dict[str, Any] | None:
    result = _git("show", f"{ref}:{path}", check=False)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def _revision_oracle_paths(ref: str) -> set[str]:
    result = _git("ls-tree", "-r", "--name-only", ref, "--", ORACLE_PREFIX, check=False)
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip().endswith(".json")}


def _worktree_oracle_paths() -> set[str]:
    root = REPOSITORY_ROOT / ORACLE_PREFIX
    if not root.is_dir():
        return set()
    return {path.relative_to(REPOSITORY_ROOT).as_posix() for path in root.glob("*.json")}


def _case_map(document: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if document is None:
        return {}
    return {
        str(case["case_id"]): case
        for case in document.get("assertions") or []
        if case.get("case_id")
    }


def _semantic_expectation(case: dict[str, Any]) -> dict[str, Any]:
    ignored = {"case_id", "bbox_expectation", *DECISION_KEYS}
    return {key: value for key, value in case.items() if key not in ignored}


def _decision_expectation(case: dict[str, Any]) -> dict[str, Any]:
    return {key: case.get(key) for key in sorted(DECISION_KEYS) if key in case}


def _change_line(label: str, before: Any, after: Any) -> str:
    return (
        f"{label}: "
        f"{json.dumps(before, ensure_ascii=False, sort_keys=True)} -> "
        f"{json.dumps(after, ensure_ascii=False, sort_keys=True)}"
    )


def _print_case_changes(base_ref: str) -> None:
    old_paths = _revision_oracle_paths(base_ref)
    new_paths = _worktree_oracle_paths()
    added: list[str] = []
    removed: list[str] = []
    changed_decision: list[str] = []
    changed_semantic: list[str] = []
    changed_bbox: list[str] = []

    for path in sorted(old_paths | new_paths):
        old_document = _read_revision_json(base_ref, path)
        new_document = _read_worktree_json(path)
        contract = str(
            (new_document or old_document or {}).get("contract_key")
            or Path(path).stem.upper()
        )
        old_cases = _case_map(old_document)
        new_cases = _case_map(new_document)
        added.extend(f"{contract}:{case_id}" for case_id in sorted(new_cases.keys() - old_cases.keys()))
        removed.extend(f"{contract}:{case_id}" for case_id in sorted(old_cases.keys() - new_cases.keys()))
        for case_id in sorted(old_cases.keys() & new_cases.keys()):
            label = f"{contract}:{case_id}"
            old_case = old_cases[case_id]
            new_case = new_cases[case_id]
            old_decision = _decision_expectation(old_case)
            new_decision = _decision_expectation(new_case)
            if old_decision != new_decision:
                changed_decision.append(_change_line(label, old_decision, new_decision))
            old_semantic = _semantic_expectation(old_case)
            new_semantic = _semantic_expectation(new_case)
            if old_semantic != new_semantic:
                changed_semantic.append(_change_line(label, old_semantic, new_semantic))
            old_bbox = old_case.get("bbox_expectation")
            new_bbox = new_case.get("bbox_expectation")
            if old_bbox != new_bbox:
                changed_bbox.append(_change_line(label, old_bbox, new_bbox))

    categories = (
        ("ADDED_CASES", added),
        ("REMOVED_CASES", removed),
        ("CHANGED_EXPECTED_DECISION", changed_decision),
        ("CHANGED_SEMANTIC_EXPECTATION", changed_semantic),
        ("CHANGED_BBOX_EXPECTATION", changed_bbox),
    )
    print("GOLDEN SEMANTIC DIFF")
    for title, cases in categories:
        print(f"  {title}: {len(cases)}")
        for case in cases:
            print(f"    {case}")


def _print_manifest_changes(base_ref: str) -> None:
    old = _read_revision_json(base_ref, MANIFEST_PATH) or {}
    new = _read_worktree_json(MANIFEST_PATH) or {}
    keys = ("baseline_version", "created_at", "contract_count", "assertion_count")
    changes = [(key, old.get(key), new.get(key)) for key in keys if old.get(key) != new.get(key)]
    print(f"GOLDEN MANIFEST DIFF: {len(changes)}")
    for key, before, after in changes:
        print(f"  {key}: {before!r} -> {after!r}")


def main() -> int:
    working_tree_names = _working_tree_names()
    base_ref = _select_base_ref(working_tree_names)
    names = _changed_names(base_ref, working_tree_names)
    protected = sorted(name for name in names if _is_protected(name))
    if not protected:
        print("GOLDEN_BASELINE_UNCHANGED")
        return 0

    print("GOLDEN_BASELINE_CHANGED")
    print(f"Comparison base: {base_ref}")
    print("HIGH_RISK_GOLDEN_FILES:")
    for name in protected:
        print(f"  {name}")
    _print_manifest_changes(base_ref)
    _print_case_changes(base_ref)

    if os.environ.get(APPROVAL_ENV, "").strip() != "1":
        print(
            f"Golden change requires explicit human approval: set {APPROVAL_ENV}=1 only after review"
        )
        print("Approval never generates or overwrites Golden files")
        return 2
    print("Golden baseline change approval acknowledged; no files were generated or overwritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
