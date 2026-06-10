from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_layout_quality import _actual_payload, _clause_order_summary
from app.models import Document
from app.services.clause_splitter import ClauseSplitter


def main() -> int:
    parser = argparse.ArgumentParser(description="Import approved layout regression cases from task storage.")
    parser.add_argument("task_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--promote-current", action="store_true")
    args = parser.parse_args()
    imported = import_cases(
        args.task_root,
        args.output_root,
        approved_by=args.approved_by,
        promote_current=args.promote_current,
    )
    print(json.dumps({"imported": imported}, ensure_ascii=False, indent=2))
    return 0


def import_cases(
    task_root: Path,
    output_root: Path,
    *,
    approved_by: str,
    promote_current: bool = False,
) -> list[str]:
    output_root.mkdir(parents=True, exist_ok=True)
    imported: list[str] = []
    seen: set[tuple[str, int]] = set()
    for structure_path in sorted(task_root.glob("*/ocr/*_ppstructure_raw.json")):
        base_name = structure_path.name.removesuffix("_ppstructure_raw.json")
        ocr_path = structure_path.with_name(f"{base_name}_ppocrv5_raw.json")
        pdf_path = structure_path.parent.parent / "uploads" / f"{base_name}.pdf"
        key = (structure_path.name, structure_path.stat().st_size)
        if key in seen or not ocr_path.exists() or not pdf_path.exists():
            continue
        seen.add(key)
        case_id = _case_id(base_name, pdf_path)
        case_dir = output_root / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf_path, case_dir / "source.pdf")
        _gzip_copy(structure_path, case_dir / "ppstructure.json.gz")
        _gzip_copy(ocr_path, case_dir / "ppocrv5.json.gz")
        manifest = {
            "case_id": case_id,
            "source_name": pdf_path.name,
            "source_sha256": _sha256(pdf_path),
            "approved_by": approved_by,
            "approved_at": datetime.now(UTC).isoformat(),
            "pdf": "source.pdf",
            "ppstructure": "ppstructure.json.gz",
            "ppocrv5": "ppocrv5.json.gz",
            "mode": "v3",
        }
        (case_dir / "case.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        candidate = _actual_payload(case_dir)
        candidate_path = case_dir / ("expected.json" if promote_current else "candidate_expected.json")
        candidate_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
        clauses = ClauseSplitter().split(Document.model_validate(candidate), "L")
        clause_payload = [_clause_order_summary(clause) for clause in clauses]
        clause_path = case_dir / ("expected_clauses.json" if promote_current else "candidate_expected_clauses.json")
        clause_path.write_text(json.dumps(clause_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        imported.append(case_id)
    return imported


def _case_id(base_name: str, pdf_path: Path) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", base_name).strip("-")[:48] or "layout-case"
    return f"{safe_name}-{_sha256(pdf_path)[:10]}"


def _gzip_copy(source: Path, target: Path) -> None:
    with source.open("rb") as input_stream, gzip.open(target, "wb", compresslevel=9) as output_stream:
        shutil.copyfileobj(input_stream, output_stream)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
