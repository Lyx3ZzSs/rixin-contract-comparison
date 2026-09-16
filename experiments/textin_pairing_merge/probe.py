"""Controlled TextIn experiments. Build/analyze are offline; run calls TextIn.

No application imports, database writes, or credentials in experiment artifacts.
Use --help for subcommands. Keep this directory and output evidence together.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/analysis/textin-pairing-merge-20260915"
PDFS = ROOT / "output/pdf/textin-pairing-merge-20260915"
API = "https://doc-compare.intsig.com/api/contracts/v3/comparison/external"
FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def line(text, y, page=0, x=60):
    return {"kind": "line", "text": text, "x": x, "y": y, "page": page}


def table(rows, y=610, page=0):
    return {"kind": "table", "rows": rows, "y": y, "page": page}


def cases():
    start = line("本文件为合成实验样本，所有单位和数据均为虚构。", 715)
    end = line("双方确认本实验文本仅用于文档处理技术验证。", 130)
    a = "设备交付：供应方应在收到通知后七日内完成安装调试。"
    b = "质量保证：验收合格后提供两年的免费维修服务。"
    c = "争议处理：双方应当先行协商解决履约过程中出现的问题。"
    stable = "资料归档：项目资料由指定人员保管并定期检查完整性。"
    amount = "服务费用为10000元，付款期限为30日。"
    amount_new = "服务费用为20000元，付款期限为45日。"
    rows = [["项目", "数量", "金额"], ["主机", "12", "10000"], ["终端", "24", "20000"]]
    entries = []

    def add(key, question, left, right, edits, notes="", pages=1):
        entries.append({"id": key, "question": question, "left": [dict(start), *left, dict(end)],
                        "right": [dict(start), *right, dict(end)], "edits": edits, "notes": notes,
                        "page_count_min": pages})

    add("C00_identical", "完全相同输入是否零变化", [line(a, 650), line(b, 600)],
        [line(a, 650), line(b, 600)], [])
    add("M01_same_line", "同一行两个被未改文字隔开的数值修改如何归组", [line(amount, 630)],
        [line(amount_new, 630)], [{"old": "10000", "new": "20000"}, {"old": "30", "new": "45"}])
    add("M02_adjacent_insert", "连续新增两个独立段落是否合并", [line(a, 665), line(c, 485)],
        [line(a, 665), line(b, 605), line(stable, 545), line(c, 485)],
        [{"insert": b}, {"insert": stable}])
    add("M03_anchor_between", "相同的两个新增段落被未改段落隔开后是否仍合并",
        [line(a, 665), line(c, 545)],
        [line(a, 665), line(b, 605), line(c, 545), line(stable, 485)],
        [{"insert": b}, {"insert": stable}],
        "与M02新增文字相同，但未改锚点位置不同；并非仅改变空白距离。")
    add("M04_multiline", "同一视觉段落两行的数值修改如何归组",
        [line("服务费用为10000元，双方按照约定履行各自义务。", 630),
         line("付款期限为30日，其他约定继续有效。", 612)],
        [line("服务费用为20000元，双方按照约定履行各自义务。", 630),
         line("付款期限为45日，其他约定继续有效。", 612)],
        [{"old": "10000", "new": "20000"}, {"old": "30", "new": "45"}])
    add("M05_table_cells", "同一表格行两个单元格修改是否合并", [table(rows)],
        [table([rows[0], ["主机", "13", "11000"], rows[2]])],
        [{"row": 1, "col": 1, "old": "12", "new": "13"},
         {"row": 1, "col": 2, "old": "10000", "new": "11000"}])
    add("P01_swap", "交换两个独特段落时跟随文字还是槽位",
        [line(a, 650), line(b, 585), line(c, 520)],
        [line(b, 650), line(a, 585), line(c, 520)], [{"move": a, "from_y": 650, "to_y": 585},
                                                  {"move": b, "from_y": 585, "to_y": 650}])
    add("P02_swap_edit", "交换段落并修改其中一处时能否恢复原段落身份",
        [line(a, 650), line(b, 585), line(c, 520)],
        [line(b.replace("两年", "三年"), 650), line(a, 585), line(c, 520)],
        [{"move": a}, {"move": b}, {"old": "两年", "new": "三年"}])
    repeat = "维护费用为10000元，服务期限为十二个月。"
    add("P03_repeated", "三个完全相同段落中间改数字时对应哪个出现位置",
        [line(repeat, 650), line(repeat, 585), line(repeat, 520)],
        [line(repeat, 650), line(repeat.replace("10000", "20000"), 585), line(repeat, 520)],
        [{"old": "10000", "new": "20000", "occurrence": 2, "y": 585}])
    add("P04_role_values", "主体标签固定、金额互换时跟随标签还是金额",
        [line("采购方甲公司：应付费用为12345元。", 650), line("供货方乙公司：应付费用为67890元。", 585)],
        [line("采购方甲公司：应付费用为67890元。", 650), line("供货方乙公司：应付费用为12345元。", 585)],
        [{"role": "采购方甲公司", "old": "12345", "new": "67890"},
         {"role": "供货方乙公司", "old": "67890", "new": "12345"}])
    # Both sides have the same number of pages and identical fixed end anchor.
    add("P05_repage", "文字顺序不变仅把一段移到下一页是否报内容变化",
        [line(a, 650), line(b, 585), line(c, 520), line(stable, 300, page=1)],
        [line(a, 650), line(b, 585), line(c, 650, page=1), line(stable, 300, page=1)],
        [{"layout_only": c, "from_page": 0, "to_page": 1}], pages=2)
    # Place the final anchor on the last page so reading order is unchanged.
    entries[-1]["left"][-1]["page"] = 1
    entries[-1]["right"][-1]["page"] = 1
    add("P06_table_split", "同表内容不变仅跨页拆开时是否仍建立对应",
        [table(rows), line(stable, 300, page=1)],
        [table(rows[:2]), table(rows[2:], y=680, page=1), line(stable, 300, page=1)],
        [{"layout_only": "终端所在行", "from_page": 0, "to_page": 1}], pages=2)
    entries[-1]["left"][-1]["page"] = 1
    entries[-1]["right"][-1]["page"] = 1
    return entries


def build():
    import fitz
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from PIL import Image, ImageDraw

    if (OUT / "runs").exists():
        raise SystemExit("Evidence runs exist: do not rebuild their inputs in place.")
    pdfmetrics.registerFont(TTFont("ProbeCN", FONT))
    PDFS.mkdir(parents=True, exist_ok=True)
    evidence = []
    thumbnails = []
    for case in cases():
        item = {k: v for k, v in case.items() if k not in ("left", "right")}
        item["inputs"] = {}
        for side in ("left", "right"):
            path = PDFS / f'{case["id"]}-{side}.pdf'
            p = canvas.Canvas(str(path), pagesize=(595, 842), invariant=1, pageCompression=1)
            p.setTitle("合成合同验证样本")
            p.setAuthor("Controlled experiment")
            objects = case[side]
            expected_text = []
            for page_no in range(case["page_count_min"]):
                p.setFont("ProbeCN", 16)
                if page_no == 0:
                    p.drawString(60, 760, "合同验证样本（合成数据）")
                    expected_text.append("合同验证样本（合成数据）")
                p.setFont("ProbeCN", 12)
                for obj in objects:
                    if obj["page"] != page_no:
                        continue
                    if obj["kind"] == "line":
                        assert obj["x"] + pdfmetrics.stringWidth(obj["text"], "ProbeCN", 12) < 555
                        p.drawString(obj["x"], obj["y"], obj["text"])
                        expected_text.append(obj["text"])
                    else:
                        widths = [210, 100, 120]
                        xs = [60, 270, 370, 490]
                        top = obj["y"]
                        for j in range(len(obj["rows"]) + 1):
                            p.line(60, top - j * 36, 490, top - j * 36)
                        for x in xs:
                            p.line(x, top, x, top - len(obj["rows"]) * 36)
                        for r, row in enumerate(obj["rows"]):
                            for col, text in enumerate(row):
                                assert pdfmetrics.stringWidth(text, "ProbeCN", 12) < widths[col] - 16
                                p.drawString(xs[col] + 8, top - r * 36 - 24, text)
                                expected_text.append(text)
                p.showPage()
            p.save()
            with fitz.open(path) as doc:
                extracted = "".join(pg.get_text() for pg in doc)
                assert "".join(expected_text) == "".join(extracted.split()), path
                for page_no, page in enumerate(doc):
                    pix = page.get_pixmap(matrix=fitz.Matrix(0.60, 0.60), alpha=False)
                    thumb = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    thumbnails.append((f'{case["id"]} {side} p{page_no+1}', thumb))
            item["inputs"][side] = {"path": str(path.relative_to(ROOT)), "sha256": sha(path),
                                    "bytes": path.stat().st_size, "objects": objects,
                                    "expected_text": expected_text}
        evidence.append(item)
    save(OUT / "manifest.json", {"schema": "textin-controlled-v1", "created_at": now(),
                                 "font": FONT, "font_sha256": sha(Path(FONT)), "cases": evidence})
    for batch in range((len(thumbnails) + 7) // 8):
        sheet = Image.new("RGB", (4 * 365, 2 * 540), "#dadada")
        draw = ImageDraw.Draw(sheet)
        for i, (label, thumb) in enumerate(thumbnails[batch*8:batch*8+8]):
            x, y = (i % 4)*365, (i // 4)*540
            draw.text((x+5, y+5), label, fill="black")
            sheet.paste(thumb, (x+4, y+25))
        sheet.save(OUT / f'contact-sheet-{batch+1}.png')
    print(json.dumps({"cases": len(evidence), "pdf_count": 2*len(evidence), "manifest": str(OUT / 'manifest.json')}))


def credentials(env_file):
    from dotenv import dotenv_values
    values = {**dotenv_values(env_file), **os.environ}
    app_id = values.get("TEXTIN_APP_ID") or values.get("TEXTIN_APPID")
    secret = values.get("TEXTIN_SECRET_CODE") or values.get("TEXTIN_SECRET")
    if not app_id or not secret:
        raise SystemExit("Missing TEXTIN_APP_ID / TEXTIN_SECRET_CODE; no external request sent.")
    return {"x-ti-app-id": app_id, "x-ti-secret-code": secret}


def run(args):
    import requests
    auth = credentials(args.env_file)
    manifest = read(OUT / "manifest.json")
    selected = set(args.cases.split(",")) if args.cases else {c["id"] for c in manifest["cases"]}
    unknown = selected - {c["id"] for c in manifest["cases"]}
    if unknown:
        raise SystemExit(f"Unknown cases: {sorted(unknown)}")
    jobs = [(c, merge, rep) for c in manifest["cases"] if c["id"] in selected
            for merge in (0, 1) for rep in range(1, args.repeats+1)]
    random.Random(915).shuffle(jobs)
    session = requests.Session()
    session.headers.update(auth)
    for case, merge, rep in jobs:
        job = f'{case["id"]}-m{merge}-r{rep}'
        folder = OUT / "runs" / job
        if (folder / "result.json").exists():
            continue
        params = {"creator": "rixin-synthetic-pairing-merge", "token_mode": 1,
                  "convert_arg": {"remove_stamp": 0, "remove_comments": 0,
                                  "remove_headerfooter": 0, "remove_symbol": 0, "merge_diff": merge},
                  "config": {"use_pdf_parser": "true", "remove_watermark": "false"}}
        for v in case["inputs"].values():
            assert sha(ROOT / v["path"]) == v["sha256"], "Input hash changed"
        payload = dict(params)
        for side, key in (("left", "standard_doc"), ("right", "compare_doc")):
            path = ROOT / case["inputs"][side]["path"]
            payload[key] = [{"filename": path.name, "filedata": base64.b64encode(path.read_bytes()).decode()}]
        create_path = folder / "create.json"
        meta_path = folder / "metadata.json"
        if not create_path.exists():
            if meta_path.exists():
                raise SystemExit(f"{job}: create outcome uncertain. Reconcile before resubmission.")
            save(meta_path, {"job": job, "case_id": case["id"], "merge_diff": merge,
                             "repeat": rep, "started_at": now(), "parameters": params,
                             "inputs": {s: {k:v for k,v in obj.items() if k in ('path','sha256','bytes')}
                                        for s,obj in case["inputs"].items()},
                             "create_state": "about_to_send"})
            try:
                response = session.post(API + "/create", json=payload, timeout=(15, 60), allow_redirects=False)
                response.raise_for_status()
                created = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise SystemExit(f"{job}: uncertain create ({type(exc).__name__}); not retried.") from None
            save(create_path, created)
        created = read(create_path)
        if created.get("code") != 200 or not created.get("data", {}).get("task_id"):
            print(json.dumps({"job": job, "create_code": created.get("code"), "message": created.get("msg")}, ensure_ascii=False))
            raise SystemExit("Create rejected; batch stopped.")
        task_id = created["data"]["task_id"]
        started = time.monotonic()
        poll_log = []
        while time.monotonic() - started < 240:
            try:
                response = session.get(API + "/diff_info", params={"task_id": task_id, "with_diff_result": "true"},
                                       timeout=(15, 45), allow_redirects=False)
                response.raise_for_status()
                result = response.json()
            except (requests.RequestException, ValueError) as exc:
                poll_log.append({"at": now(), "error_type": type(exc).__name__})
                save(folder / "polls.json", poll_log)
                time.sleep(3)
                continue
            code, status = result.get("code"), result.get("data", {}).get("status")
            poll_log.append({"at": now(), "code": code, "status": status})
            save(folder / "polls.json", poll_log)
            if code == 200 and status == 1:
                assert len(result["data"]["result"]) == 1
                assert "diff_result" in result["data"]["result"][0]
                save(folder / "result.json", result)
                meta = read(meta_path)
                completed = datetime.now(timezone.utc)
                meta.update({"completed_at": completed.isoformat(), "task_id": task_id,
                             "wall_seconds_since_initial_start": (completed-datetime.fromisoformat(meta["started_at"])).total_seconds(),
                             "result_sha256": sha(folder / "result.json"), "create_state": "complete"})
                save(meta_path, meta)
                print(json.dumps({"job": job, "task_id": task_id,
                                  "diff_info": result["data"]["result"][0]["diff_info"]}, ensure_ascii=False), flush=True)
                break
            if status == -1 or code not in (200, 209):
                save(folder / "failure.json", result)
                raise SystemExit(f"{job}: polling failed, code={code}, status={status}")
            time.sleep(2)
        else:
            raise SystemExit(f"{job}: poll timeout; rerun resumes existing task.")
        time.sleep(1)


def leaves(value):
    if isinstance(value, dict):
        if "doc_index" in value and "text" in value:
            yield value
        else:
            for v in value.values():
                yield from leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from leaves(v)


def analyze():
    summaries = []
    for path in sorted((OUT / "runs").glob("*/result.json")):
        raw = read(path)
        result = raw["data"]["result"][0]
        items = []
        for i, item in enumerate(result["diff_result"], 1):
            items.append({"ordinal": i, "type": item["type"], "status": item["status"],
                          "element_id": item.get("element_id"),
                          "diff_leaves": list(leaves(item.get("diff", []))),
                          "area_leaves": list(leaves(item.get("area", []))),
                          "diff": item.get("diff", [])})
        meta = read(path.parent / "metadata.json")
        statuses = Counter(x["status"] for x in items)
        summaries.append({"job": path.parent.name, "case_id": meta["case_id"],
                          "merge_diff": meta["merge_diff"], "repeat": meta["repeat"],
                          "task_id": meta["task_id"], "result_sha256": sha(path),
                          "metrics": raw["data"].get("metrics"),
                          "diff_info": result["diff_info"], "statuses": dict(statuses),
                          "change_count": sum(v for k,v in statuses.items() if k in ("INSERT","DELETE","CHANGE")),
                          "items": items})
    save(OUT / "analysis.json", summaries)
    paired = []
    for case_id in sorted({s["case_id"] for s in summaries}):
        for rep in sorted({s["repeat"] for s in summaries if s["case_id"] == case_id}):
            pair = {s["merge_diff"]: s for s in summaries if s["case_id"] == case_id and s["repeat"] == rep}
            if set(pair) != {0, 1}:
                continue

            def leaf_bag(row):
                return Counter(json.dumps({k: v.get(k) for k in
                               ("doc_index", "page_index", "text", "char_polygons", "sub_info")},
                               ensure_ascii=False, sort_keys=True)
                               for item in row["items"] for v in item["diff_leaves"])

            a, b = leaf_bag(pair[0]), leaf_bag(pair[1])
            paired.append({"case_id": case_id, "repeat": rep,
                           "change_count_off": pair[0]["change_count"],
                           "change_count_on": pair[1]["change_count"],
                           "diff_leaf_multiset_identical": a == b,
                           "off_only_leaves": [json.loads(x) for x in (a-b).elements()],
                           "on_only_leaves": [json.loads(x) for x in (b-a).elements()],
                           "interpretation_limit": "分段变化会改变叶节点比较；不相等不直接代表漏报或配对变化。"})
    save(OUT / "merge-pairs.json", paired)
    lines = ["# TextIn 受控实验原始观察", "", "此文件自动整理原始响应，不把输出当作正确答案。页码及框保留供应商原始值。", ""]
    for row in summaries:
        lines += [f'## {row["job"]}', "", f'任务：`{row["task_id"]}`；变化数：{row["change_count"]}；原始计数：`{row["diff_info"]}`。', ""]
        for item in row["items"]:
            sides = {}
            for side in (1, 2):
                sides[side] = [{"text": v["text"], "page_index": v.get("page_index"),
                                "line_index": v.get("line_index"), "boxes": v.get("char_polygons")}
                               for v in item["diff_leaves"] if v["doc_index"] == side]
            lines += [f'- {item["ordinal"]}. {item["type"]} / {item["status"]}: '
                      f'{json.dumps(sides, ensure_ascii=False)}']
        lines.append("")
    (OUT / "observations.md").write_text("\n".join(lines))
    print(json.dumps({"completed_runs": len(summaries), "output": str(OUT / 'analysis.json')}))


def verify_inputs():
    import fitz
    manifest = read(OUT / "manifest.json")
    by_id = {c["id"]: c for c in manifest["cases"]}
    texts = {}
    for case in manifest["cases"]:
        for side, obj in case["inputs"].items():
            path = ROOT / obj["path"]
            assert sha(path) == obj["sha256"]
            with fitz.open(path) as doc:
                assert len(doc) == case["page_count_min"]
                text = "".join("".join(page.get_text().split()) for page in doc)
                assert text == "".join(obj["expected_text"])
                texts[case["id"], side] = text
    checks = {}
    for key in ("C00_identical", "P05_repage", "P06_table_split"):
        checks[key + "_same_reading_order_text"] = texts[key, "left"] == texts[key, "right"]
    checks["C00_identical_same_pdf_bytes"] = by_id["C00_identical"]["inputs"]["left"]["sha256"] == by_id["C00_identical"]["inputs"]["right"]["sha256"]
    checks["P01_swap_same_character_multiset"] = Counter(texts["P01_swap", "left"]) == Counter(texts["P01_swap", "right"])
    checks["P04_roles_same_character_multiset"] = Counter(texts["P04_role_values", "left"]) == Counter(texts["P04_role_values", "right"])
    checks["all_24_inputs_hash_and_text_verified"] = len(texts) == 24
    assert all(checks.values()), checks
    save(OUT / "input-verification.json", {"checked_at": now(), "checks": checks,
                                          "pdf_count": len(texts), "manifest_sha256": sha(OUT / "manifest.json")})
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    runner = sub.add_parser("run")
    runner.add_argument("--env-file", type=Path, default=ROOT / ".env")
    runner.add_argument("--cases", help="Comma-separated manifest case IDs; default all 12")
    runner.add_argument("--repeats", type=int, choices=range(1, 4), default=1)
    sub.add_parser("analyze")
    sub.add_parser("verify-inputs")
    args = parser.parse_args()
    {"build": build, "run": lambda: run(args), "analyze": analyze, "verify-inputs": verify_inputs}[args.command]()
