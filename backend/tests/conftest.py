from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def authenticated_admin_for_existing_api_tests():
    from app.auth.dependencies import get_current_user
    from app.main import app, auth_runtime
    from auth_helpers import ADMIN

    original_prewarm = auth_runtime.prewarm
    auth_runtime.prewarm = lambda: None
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        auth_runtime.prewarm = original_prewarm
