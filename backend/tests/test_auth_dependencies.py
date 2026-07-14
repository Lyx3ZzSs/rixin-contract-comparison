from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.errors import IdentityProviderUnavailable, InvalidBearerToken
from app.auth.models import AGENT_ADMIN, CurrentUser

from auth_helpers import ADMIN, USER_A


class FakeValidator:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def validate(self, token: str) -> CurrentUser:
        self.tokens.append(token)
        if token == "invalid":
            raise InvalidBearerToken()
        if token == "unavailable":
            raise IdentityProviderUnavailable()
        return ADMIN if token == "admin" else USER_A


class FakeRuntime:
    def __init__(self) -> None:
        self.validator = FakeValidator()


def test_bearer_dependency_returns_stable_auth_errors_and_role_checks() -> None:
    from app.auth.dependencies import get_auth_runtime, get_current_user, require_roles

    app = FastAPI()

    @app.get("/me")
    def me(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        return user

    @app.get("/admin")
    def admin(user: CurrentUser = Depends(require_roles(AGENT_ADMIN))) -> CurrentUser:
        return user

    runtime = FakeRuntime()
    app.dependency_overrides[get_auth_runtime] = lambda: runtime
    client = TestClient(app)

    missing = client.get("/me")
    basic = client.get("/me", headers={"Authorization": "Basic abc"})
    valid = client.get("/me", headers={"Authorization": "Bearer valid"})
    user_forbidden = client.get("/admin", headers={"Authorization": "Bearer user"})
    admin_allowed = client.get("/admin", headers={"Authorization": "Bearer admin"})
    invalid = client.get("/me", headers={"Authorization": "Bearer invalid"})
    unavailable = client.get("/me", headers={"Authorization": "Bearer unavailable"})

    assert missing.status_code == 401
    assert basic.status_code == 401
    assert valid.status_code == 200
    assert valid.json()["sub"] == "user-a-sub"
    assert user_forbidden.status_code == 403
    assert admin_allowed.status_code == 200
    assert invalid.status_code == 401
    assert unavailable.status_code == 503
    assert all(response.headers["www-authenticate"] == "Bearer" for response in (missing, basic, invalid))
    assert "valid" not in str(invalid.json())
    assert runtime.validator.tokens == ["valid", "user", "admin", "invalid", "unavailable"]
