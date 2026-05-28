from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.models import CompareTask
from app.models_extraction import ExtractionTask


@dataclass
class ImportStats:
    imported: int = 0
    skipped: int = 0
    failed: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import local JSON task records into PostgreSQL.")
    parser.add_argument("--tasks-dir", type=Path, default=settings.tasks_dir, help="Directory containing *.json tasks.")
    parser.add_argument("--database-url", default=settings.database_url, help="PostgreSQL connection URL.")
    parser.add_argument("--dry-run", action="store_true", help="Validate input files without writing to PostgreSQL.")
    return parser.parse_args()


def load_task_payload(path: Path) -> tuple[str, CompareTask | ExtractionTask]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("task JSON root must be an object")
    if data.get("task_type") == "extraction":
        return "extraction", ExtractionTask(**data)
    return "compare", CompareTask(**data)


def import_tasks(tasks_dir: Path, database_url: str, *, dry_run: bool = False) -> ImportStats:
    stats = ImportStats()
    if not tasks_dir.exists():
        raise FileNotFoundError(f"Tasks directory does not exist: {tasks_dir}")

    repository: Any | None = None
    if not dry_run:
        if not database_url:
            raise RuntimeError("DATABASE_URL or --database-url is required unless --dry-run is used.")
        from app.infrastructure.postgres_task_repository import PostgresTaskRepository

        repository = PostgresTaskRepository(database_url=database_url)

    for path in sorted(tasks_dir.glob("*.json")):
        try:
            task_type, task = load_task_payload(path)
            if repository is not None:
                if task_type == "extraction":
                    repository.save_extraction_task(task)
                else:
                    repository.save_compare_task(task)
            stats.imported += 1
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            stats.skipped += 1
            print(f"skip invalid task {path.name}: {exc}", file=sys.stderr)
        except Exception as exc:
            stats.failed += 1
            print(f"failed to import {path.name}: {exc}", file=sys.stderr)
    return stats


def main() -> int:
    args = parse_args()
    stats = import_tasks(args.tasks_dir, args.database_url, dry_run=args.dry_run)
    mode = "validated" if args.dry_run else "imported"
    print(f"{mode}: {stats.imported}; skipped: {stats.skipped}; failed: {stats.failed}")
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
