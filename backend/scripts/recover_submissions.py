from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infrastructure.recovery_store import default_recovery_store


def main() -> int:
    return 0 if default_recovery_store.recover_all() else 1


if __name__ == "__main__":
    raise SystemExit(main())
