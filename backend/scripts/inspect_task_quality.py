from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _main_dir(path: Path) -> Path:
    return path if path.is_dir() else path.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a compare task quality summary and suspicious diffs.")
    parser.add_argument("task", type=str, help="任务ID（例如 05616...）或任务目录路径")
    parser.add_argument(
        "--task-dir",
        type=Path,
        help="直接指定任务目录（与 --task-id 互斥使用）",
    )
    parser.add_argument(
        "--task-id",
        type=str,
        help="与 --task-dir 共同或单独使用，优先使用显式目录",
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path("storage/tasks"),
        help="任务根目录（默认 storage/tasks）",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="输出可疑差异条数（默认 20）",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="输出格式（默认 text）",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8000",
        help="生成复核命令时的 API 基础地址",
    )
    args = parser.parse_args()

    task_dir = resolve_task_dir(
        task_arg=args.task,
        task_id=args.task_id,
        explicit_task_dir=args.task_dir,
        storage_root=args.storage_root,
    )
    payload = inspect_task(task_dir, top_n=args.top, base_url=args.base_url)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        render_text(payload, args.base_url)
    return 0


def resolve_task_dir(
    task_arg: str,
    task_id: str | None = None,
    explicit_task_dir: Path | None = None,
    storage_root: Path = Path("storage/tasks"),
) -> Path:
    if explicit_task_dir:
        if not explicit_task_dir.exists():
            raise FileNotFoundError(f"任务目录不存在: {explicit_task_dir}")
        return explicit_task_dir

    candidates: list[Path] = []
    if task_id:
        candidates.append(storage_root / task_id)
    if task_arg and not task_arg.startswith("storage/"):
        candidates.append(storage_root / task_arg)
        if task_arg.endswith(".json"):
            # tolerate accidental passing task.json
            base = Path(task_arg)
            if base.name == "task.json":
                candidates.append(base.parent)
    if Path(task_arg).is_dir():
        candidates.append(Path(task_arg))

    for candidate in candidates:
        if candidate.exists():
            return _main_dir(candidate)

    raise FileNotFoundError(
        "未找到任务目录。请提供 --task-dir 或 --task-id，或直接给出任务目录路径（如 storage/tasks/<task_id>）"
    )

def inspect_task(task_dir: Path, top_n: int, base_url: str) -> dict[str, Any]:
    task = _read_json(task_dir / "task.json")
    diffs = task.get("diffs", [])
    quality = task.get("ocr_quality_summary") or {}
    profiles = list(task.get("parse_warning_details") or [])

    action_map, action_notes = _load_diff_actions(task_dir / "debug" / "diff_quality.json")
    suspicious = _collect_suspects(diffs, action_map, action_notes, top_n)

    review_flag_counts = Counter(flag for diff in diffs for flag in _to_text_list(diff.get("review_flags")))
    evidence_quality_counts = _collect_evidence_quality(diffs)

    source_counts = Counter(str(diff.get("source_type", "clause")) for diff in diffs)
    diff_type_counts = Counter(str(diff.get("diff_type", "")) for diff in diffs)
    qstatus_counts = Counter(str(diff.get("quality_status", "NEEDS_REVIEW")) for diff in diffs)
    rstatus_counts = Counter(str(diff.get("review_status", "UNREVIEWED")) for diff in diffs)
    severity_counts = Counter(str(item.get("severity", "")) for item in profiles if item.get("severity"))

    warning_codes = Counter(item.get("code", "unknown") for item in profiles)
    warning_reason_counts = Counter(item.get("message", "unknown") for item in profiles)
    risk_pages = list({item.get("page_no") for item in quality.get("profiles", []) if item.get("status") != "OK" and item.get("page_no") is not None})

    ocr_model = _read_json(task_dir / "debug" / "ocr_model_routing.json") if (task_dir / "debug" / "ocr_model_routing.json").exists() else {}
    route_summary = {
        "status": ocr_model.get("status", ""),
        "manual_review_recommended_count": ocr_model.get("manual_review_recommended_count", 0),
        "retry_recommended_count": ocr_model.get("retry_recommended_count", 0),
        "route_count": ocr_model.get("route_count", 0),
        "affected_pages_count": len(ocr_model.get("routes", [])),
    }
    high_risk_routes = [
        {
            "side": route.get("side"),
            "page_no": route.get("page_no"),
            "route": route.get("recommended_route"),
            "quality": route.get("quality_status"),
            "scores": route.get("notes", []),
            "diffs": route.get("affected_diff_ids", [])[:10],
        }
        for route in ocr_model.get("routes", [])
        if route.get("quality_status") not in {"OK", "KEEP_CURRENT"}
        or route.get("recommended_route") in {"MANUAL_REVIEW", "TABLE_REGION_RETRY", "CRITICAL_FIELD_RETRY"}
    ]

    return {
        "task_id": task.get("task_id", ""),
        "task_dir": str(task_dir),
        "status": task.get("status", ""),
        "stage": task.get("stage", ""),
        "progress_percent": task.get("progress_percent", 0),
        "summary": {
            "diff_count": len(diffs),
            "needs_review_count": len([diff for diff in diffs if diff.get("quality_status") == "NEEDS_REVIEW"]),
            "source_counts": dict(source_counts),
            "type_counts": dict(diff_type_counts),
            "quality_status_counts": dict(qstatus_counts),
            "review_status_counts": dict(rstatus_counts),
            "review_flag_counts": dict(review_flag_counts),
            "evidence_quality_counts": evidence_quality_counts,
            "high_risk_flags": {k: v for k, v in review_flag_counts.items() if "SEAL" in k or "OCR" in k or "HEADER" in k},
            "warning_codes": dict(warning_codes),
            "warning_severity_counts": {k: v for k, v in severity_counts.items() if k},
            "ocr_status": quality.get("status", ""),
            "ocr_risk_page_count": quality.get("risk_page_count", 0),
            "ocr_affected_diff_count": quality.get("affected_diff_count", 0),
            "ocr_risk_pages": risk_pages,
            "ocr_route_summary": route_summary,
        },
        "ocr_quality_top_reasons": [
            {
                "reason": reason,
                "count": count,
            }
            for reason, count in warning_reason_counts.most_common(8)
        ],
        "ocr_model_routes": high_risk_routes,
        "suspects": suspicious,
        "review_commands": _build_review_commands(task.get("task_id", ""), top_n, suspicious, base_url),
    }


def _collect_suspects(
    diffs: list[dict[str, Any]],
    action_map: dict[str, str],
    action_notes: dict[str, str],
    top_n: int,
) -> list[dict[str, Any]]:
    candidates = []
    for diff in diffs:
        diff_id = str(diff.get("diff_id", ""))
        if not diff_id:
            continue
        action = action_map.get(diff_id, "")
        evidence = _to_dict_list(diff.get("original_evidence")) + _to_dict_list(diff.get("compare_evidence"))
        low_evidence_count = sum(1 for item in evidence if item.get("evidence_quality") == "LOW")
        has_low_evidence = low_evidence_count > 0
        score = _suspect_score(diff, action, has_low_evidence)
        if score <= 0:
            continue
        candidates.append(
            {
                "score": score,
                "diff_id": diff_id,
                "action": action,
                "action_note": action_notes.get(diff_id, ""),
                "diff_type": diff.get("diff_type"),
                "source_type": diff.get("source_type"),
                "title": diff.get("title", ""),
                "clause_no": diff.get("clause_no", ""),
                "section_type": diff.get("section_type", ""),
                "quality_status": diff.get("quality_status"),
                "match_score": diff.get("match_score"),
                "match_confidence": diff.get("match_confidence"),
                "match_method": diff.get("match_method"),
                "review_flags": diff.get("review_flags", []),
                "review_flag_count": len(_to_text_list(diff.get("review_flags"))),
                "original_snippet": (diff.get("original_snippet") or "")[:160],
                "compare_snippet": (diff.get("compare_snippet") or "")[:160],
                "low_evidence_count": low_evidence_count,
                "evidence_count": len(evidence),
                "evidence_qualities": _evidence_qualities(evidence),
                "requires_review": diff.get("quality_status") == "NEEDS_REVIEW",
            }
        )

    candidates.sort(
        key=lambda item: (
            -item["score"],
            item["requires_review"] is False,
            -item["review_flag_count"],
            -(item["low_evidence_count"]),
            item["diff_id"],
        )
    )
    return candidates[:top_n]


def _suspect_score(diff: dict[str, Any], action: str, has_low_evidence: bool) -> int:
    score = 0
    source_type = str(diff.get("source_type", ""))
    flags = set(_to_text_list(diff.get("review_flags")))
    quality_status = diff.get("quality_status")

    if quality_status == "NEEDS_REVIEW":
        score += 2
    if source_type in {"header_footer", "seal"}:
        score += 3
    if action in {"non_body_change_review", "possible_ocr_noise", "non_main_contract_section"}:
        score += 4
    if action == "suppressed_low_value_noise":
        score += 3
    if action == "structural_risk_review":
        score += 2

    if "HEADER_FOOTER_REVIEW" in flags:
        score += 2
    if "SEAL_REVIEW" in flags:
        score += 2
    if "POSSIBLE_OCR_NOISE" in flags:
        score += 2
    if "LOW_CONFIDENCE_MATCH" in flags or "LOW_COVERAGE_MATCH_REVIEW" in flags:
        score += 1
    if has_low_evidence:
        score += 1

    if action == "":
        # 有些任务的 debug 文件可能没写入，仍可用质量风险信号判断
        if "OCR_REMEDIATION_MANUAL_REVIEW" in flags or "OCR_REMEDIATION_UNRESOLVED" in flags:
            score += 1
        if source_type == "clause":
            score += 1

    return score


def _build_review_commands(
    task_id: str,
    top_n: int,
    suspects: list[dict[str, Any]],
    base_url: str,
) -> list[str]:
    if not task_id:
        return []

    cmd_template = (
        "curl -X PATCH "
        '"{base_url}/api/compare/{task_id}/diffs/{diff_id}/review" '
        '-H "Content-Type: application/json" '
        '-d \'{{"review_status":"FALSE_POSITIVE","review_comment":"auto-check","reviewed_by":"auto"}}\''
    )
    cmds: list[str] = []
    for item in suspects[:top_n]:
        diff_id = item.get("diff_id", "")
        if not diff_id:
            continue
        cmds.append(
            cmd_template.format(
                base_url=base_url.rstrip("/"),
                task_id=task_id,
                diff_id=diff_id,
            )
        )
    return cmds


def _collect_evidence_quality(diffs: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for diff in diffs:
        for evidence in _to_dict_list(diff.get("original_evidence")) + _to_dict_list(diff.get("compare_evidence")):
            counts[evidence.get("evidence_quality", "UNKNOWN")] += 1
    return dict(counts)


def _evidence_qualities(evidence: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(item.get("evidence_quality", "UNKNOWN") for item in evidence))


def _load_diff_actions(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    if not path.exists():
        return {}, {}
    payload = _read_json(path)
    actions: dict[str, str] = {}
    notes: dict[str, str] = {}
    for item in payload.get("decisions", []):
        diff_id = str(item.get("diff_id") or "")
        if not diff_id:
            continue
        action = str(item.get("action", "")).strip()
        actions[diff_id] = action
        detail = item.get("detail")
        if isinstance(detail, dict) and detail:
            notes[diff_id] = json.dumps(detail, ensure_ascii=False)
    return actions, notes


def render_text(payload: dict[str, Any], base_url: str) -> None:
    summary = payload["summary"]
    task_line = [
        f"任务: {payload['task_id']}",
        f"目录: {payload['task_dir']}",
        f"状态: {payload['status']} / {payload['stage']} ({payload['progress_percent']}%)",
        f"diff总数: {summary['diff_count']}",
        f"NEEDS_REVIEW: {summary['quality_status_counts'].get('NEEDS_REVIEW', 0)}",
        f"OCR状态: {summary['ocr_status']} | 风险页: {summary['ocr_risk_page_count']} | 受影响差异: {summary['ocr_affected_diff_count']}",
    ]
    print("\n".join(task_line))

    print("\n[来源统计]")
    print(_pairs_to_text(summary["source_counts"]))
    print("[差异类型]")
    print(_pairs_to_text(summary["type_counts"]))
    print("[复核标记 Top]")
    for key, value in sorted(summary["review_flag_counts"].items(), key=lambda item: (-item[1], item[0]))[:10]:
        print(f"- {key}: {value}")

    print("\n[OCR 风险原因]")
    for item in payload.get("ocr_quality_top_reasons", []):
        print(f"- {item['reason']}: {item['count']}")

    print("\n[高风险 OCR 路由]")
    for route in (payload.get("ocr_model_routes", []) or []):
        print(
            "- {side} p{page_no}: {route} ({quality})".format(
                side=route.get("side"),
                page_no=route.get("page_no"),
                route=route.get("route"),
                quality=route.get("quality"),
            )
        )
        if route.get("diffs"):
            print(f"  关联差异: {', '.join(map(str, route['diffs']))}")

    suspects = payload.get("suspects", [])
    print(f"\n[可疑差异 Top {len(suspects)}]")
    for index, item in enumerate(suspects, start=1):
        print(
            f"{index:>2}. {item['diff_id']} | score={item['score']} | "
            f"{item['diff_type']}/{item['source_type']} | {item['quality_status']}"
        )
        print(f"    action={item['action'] or 'UNKNOWN'} | title={item['title']}")
        if item.get("action_note"):
            print(f"    note={item['action_note']}")
        print(
            f"    orig='{item['original_snippet']}' -> cmp='{item['compare_snippet']}'"
            if item["original_snippet"] or item["compare_snippet"]
            else "    [no snippets]"
        )
        flags = ", ".join(item.get("review_flags", [])[:12])
        print(f"    flags: {flags}")
        if item["evidence_count"]:
            print(f"    evidences: {item['evidence_count']}, quality={item['evidence_qualities']}")
        print(
            f"    review api: PATCH /api/compare/{payload['task_id']}/diffs/{item['diff_id']}/review"
        )

    print("\n[一键 FALSE_POSITIVE 指令]")
    for cmd in payload["review_commands"]:
        if "auto-check" in cmd:
            print(f"- {cmd}")

    if base_url:
        print(f"\n说明：上述命令中的 {base_url} 为可替换的 base_url。可先复制到终端逐条执行。")


def _pairs_to_text(values: dict[str, Any]) -> str:
    if not values:
        return "- 无"
    lines = [f"- {key}: {value}" for key, value in sorted(values.items(), key=lambda item: item[0])]
    return "\n".join(lines)


def _to_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _to_dict_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
