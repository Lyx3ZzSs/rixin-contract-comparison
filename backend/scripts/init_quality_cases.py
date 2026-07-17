from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

from app.config import settings
from app.services.quality_workbench import (
    QualityWorkbenchError,
    initialize_quality_cases,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize production quality cases from approved image seeds.")
    parser.add_argument("--cases-dir", type=Path, default=settings.quality_cases_dir)
    parser.add_argument("--seed-dir", type=Path, default=settings.quality_cases_seed_dir)
    args = parser.parse_args(argv)

    try:
        result = initialize_quality_cases(args.cases_dir, args.seed_dir)
    except QualityWorkbenchError as exc:
        payload = {
            "status": "error",
            "code": getattr(exc, "error_code", "QUALITY_CASES_INITIALIZATION_FAILED"),
            "message": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "error",
            "code": "QUALITY_CASES_INITIALIZATION_FAILED",
            "message": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {"status": "ok", "code": "QUALITY_CASES_INITIALIZED", **result},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
