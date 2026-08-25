# Keycloak OIDC Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the local fake login with Keycloak Authorization Code + PKCE, validate Bearer access tokens in FastAPI, enforce application roles, and isolate every new comparison task by the authenticated user's `sub`.

**Architecture:** The React SPA uses `react-oidc-context` over a single lazy `oidc-client-ts` `UserManager`, with session-only storage and a centralized authenticated fetch layer. FastAPI uses a discovery-backed RS256 validator, typed `CurrentUser` dependencies, and one reusable task access policy; every task subresource is checked against immutable `owner_sub`, while `agent_admin` can access all newly owned tasks.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, httpx, PyJWT 2.13 with cryptography, pytest, React 19, TypeScript 5.8, Vite 6, Vitest, oidc-client-ts, react-oidc-context, PDF.js.

## Global Constraints


---

## File and responsibility map

### Backend additions

- `backend/app/auth/models.py`: role constants, `AuthSettings`, and immutable `CurrentUser`.
- `backend/app/auth/errors.py`: stable invalid-token and identity-provider-unavailable exceptions.
- `backend/app/auth/discovery.py`: discovery/JWKS retrieval, validation, cache, refresh lock, and unknown-`kid` refresh.
- `backend/app/auth/validator.py`: fixed-algorithm JWT validation and claim-to-user mapping.
- `backend/app/auth/runtime.py`: lifecycle-owned httpx client, provider, and validator.
- `backend/app/auth/dependencies.py`: HTTP Bearer parsing, FastAPI user dependency, and role dependency factory.
- `backend/app/auth/policies.py`: task visibility and mutation policy.
- `backend/tests/auth_helpers.py`: deterministic users and RSA/JWK helpers.
- `backend/tests/test_auth_discovery.py`: discovery/JWKS cache tests.
- `backend/tests/test_auth_validator.py`: signature and claim validation tests.
- `backend/tests/test_auth_dependencies.py`: 401/403/503 dependency behavior.
- `backend/tests/test_task_access_policy.py`: owner/admin/legacy policy tests.
- `backend/tests/test_api_authz.py`: endpoint-level role and ownership matrix.

### Frontend additions

- `frontend/src/auth/config.ts`: exact OIDC environment contract and safe return-path helpers.
- `frontend/src/auth/userManager.ts`: one lazy `UserManager` using session storage.
- `frontend/src/auth/currentUser.ts`: access-token claim decoding and display-only role model.
- `frontend/src/auth/AuthGate.tsx`: initialization, callback, automatic redirect, and error states.
- `frontend/src/lib/authFetch.ts`: Bearer injection, 401 reauthentication, status-aware errors, and authenticated Blob downloads.
- `frontend/src/lib/api_sse.test.ts`: authenticated Fetch/ReadableStream SSE behavior.
- `frontend/src/auth/*.test.tsx`: OIDC bootstrap, callback, user mapping, and no-loop behavior.

### Existing files changed

- Backend: dependency manifests, `config.py`, `main.py`, `models.py`, `application/compare_tasks.py`, `api.py`, `api_quality.py`, backend test fixtures, environment examples, and README.
- Frontend: dependency manifests, `main.tsx`, `App.tsx`, `lib/state.tsx`, `lib/api.ts`, `lib/api_sse.ts`, `lib/hooks.ts`, `PdfDocumentViewer.tsx`, `ResultPage.tsx`, `ComparisonRecordsPage.tsx`, tests, environment examples, and Vite env types.

---

### Task 1: Backend authentication configuration and principal model

**Files:**
- Create: `backend/app/auth/__init__.py`
- Create: `backend/app/auth/models.py`
- Create: `backend/app/auth/errors.py`
- Create: `backend/tests/test_auth_models.py`
- Modify: `backend/app/config.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements.txt`
- Modify: `backend/uv.lock`

**Interfaces:**
- Produces: `AuthSettings`, `CurrentUser`, `AGENT_ADMIN`, `AGENT_MANAGER`, `AGENT_USER`, `APP_ROLES`, `InvalidBearerToken`, and `IdentityProviderUnavailable`.
- `CurrentUser.has_any_role(*roles: str) -> bool` is consumed by Tasks 3–5.
- `Settings.auth: AuthSettings` is consumed by Task 2.

- [ ] **Step 1: Add failing configuration and principal tests**

```python
# backend/tests/test_auth_models.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.auth.models import AGENT_ADMIN, AuthSettings, CurrentUser
from app.config import Settings


def test_settings_builds_exact_rs256_auth_configuration(tmp_path) -> None:
    settings = Settings(
        storage_dir=tmp_path / "storage",
        oidc_discovery_url="http://idp/realms/company/.well-known/openid-configuration",
        oidc_issuer="http://idp/realms/company",
        oidc_audience="rixin-contract-comparison-api",
        oidc_resource_client_id="rixin-contract-comparison-api",
        oidc_allowed_algorithms="RS256",
    )

    assert settings.auth == AuthSettings(
        discovery_url="http://idp/realms/company/.well-known/openid-configuration",
        issuer="http://idp/realms/company",
        audience="rixin-contract-comparison-api",
        resource_client_id="rixin-contract-comparison-api",
        allowed_algorithms=("RS256",),
    )
    assert settings.auth.is_configured


def test_settings_rejects_any_algorithm_other_than_rs256(tmp_path) -> None:
    with pytest.raises(ValidationError, match="OIDC_ALLOWED_ALGORITHMS must be exactly RS256"):
        Settings(storage_dir=tmp_path / "storage", oidc_allowed_algorithms="RS256,HS256")


def test_current_user_uses_sub_and_fixed_client_roles() -> None:
    user = CurrentUser(
        sub="keycloak-user-id",
        preferred_username="alice",
        name="Alice",
        email="alice@example.test",
        department_code="IT",
        department_name="信息技术部",
        roles=frozenset({AGENT_ADMIN}),
    )

    assert user.user_id == "keycloak-user-id"
    assert user.display_name == "Alice"
    assert user.has_any_role(AGENT_ADMIN)
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `cd backend && python -m pytest tests/test_auth_models.py -q`

Expected: collection fails because `app.auth.models` does not exist.

- [ ] **Step 3: Add PyJWT with RSA support**

Run: `cd backend && uv add 'PyJWT[crypto]>=2.13,<3'`

Expected: `pyproject.toml` and `uv.lock` add PyJWT and cryptography-compatible dependencies.

Add the same runtime requirement to `backend/requirements.txt`:

```text
PyJWT[crypto]>=2.13,<3
```

- [ ] **Step 4: Implement auth errors and models**

```python
# backend/app/auth/errors.py
class InvalidBearerToken(Exception):
    """The presented bearer token cannot be trusted."""


class IdentityProviderUnavailable(Exception):
    """OIDC metadata or signing keys are temporarily unavailable."""
```

```python
# backend/app/auth/models.py
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

AGENT_ADMIN = "agent_admin"
AGENT_MANAGER = "agent_manager"
AGENT_USER = "agent_user"
APP_ROLES = frozenset({AGENT_ADMIN, AGENT_MANAGER, AGENT_USER})


class AuthSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    discovery_url: str = ""
    issuer: str = ""
    audience: str = ""
    resource_client_id: str = ""
    allowed_algorithms: tuple[str, ...] = ("RS256",)

    @field_validator("discovery_url", "issuer")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if normalized and not normalized.startswith(("http://", "https://")):
            raise ValueError("OIDC URLs must start with http:// or https://")
        return normalized

    @property
    def is_configured(self) -> bool:
        return bool(self.discovery_url and self.issuer and self.audience and self.resource_client_id)


class CurrentUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    sub: str
    preferred_username: str = ""
    name: str = ""
    email: str = ""
    department_code: str = ""
    department_name: str = ""
    roles: frozenset[str] = frozenset()

    @field_validator("sub")
    @classmethod
    def validate_sub(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("sub is required")
        return normalized

    @property
    def user_id(self) -> str:
        return self.sub

    @property
    def display_name(self) -> str:
        return self.name or self.preferred_username or self.sub

    def has_any_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))
```

Export the public names from `backend/app/auth/__init__.py`:

```python
from app.auth.models import (
    AGENT_ADMIN,
    AGENT_MANAGER,
    AGENT_USER,
    APP_ROLES,
    AuthSettings,
    CurrentUser,
)

__all__ = [
    "AGENT_ADMIN",
    "AGENT_MANAGER",
    "AGENT_USER",
    "APP_ROLES",
    "AuthSettings",
    "CurrentUser",
]
```

- [ ] **Step 5: Wire flat environment variables into `Settings.auth`**

Add these fields to `Settings` in `backend/app/config.py`:

```python
    oidc_discovery_url: str = ""
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_resource_client_id: str = ""
    oidc_allowed_algorithms: str = "RS256"
    auth: AuthSettings = Field(default_factory=AuthSettings, exclude=True)
```

Import `AuthSettings`, validate the algorithm string, and populate the nested model in `_build_nested_and_paths`:

```python
    @field_validator("oidc_allowed_algorithms", mode="before")
    @classmethod
    def validate_oidc_algorithms(cls, value: Any) -> str:
        algorithms = ",".join(part.strip() for part in str(value or "").split(",") if part.strip())
        if algorithms != "RS256":
            raise ValueError("OIDC_ALLOWED_ALGORITHMS must be exactly RS256")
        return algorithms
```

```python
        self.auth = AuthSettings(
            discovery_url=self.oidc_discovery_url,
            issuer=self.oidc_issuer,
            audience=self.oidc_audience,
            resource_client_id=self.oidc_resource_client_id,
            allowed_algorithms=tuple(self.oidc_allowed_algorithms.split(",")),
        )
```

- [ ] **Step 6: Run focused tests**

Run: `cd backend && python -m pytest tests/test_auth_models.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit the configuration unit**

```bash
git add backend/app/auth backend/app/config.py backend/tests/test_auth_models.py backend/pyproject.toml backend/requirements.txt backend/uv.lock
git commit -m "Add OIDC authentication configuration"
```

---

### Task 2: Discovery, JWKS caching, and strict JWT validation

**Files:**
- Create: `backend/app/auth/discovery.py`
- Create: `backend/app/auth/validator.py`
- Create: `backend/tests/auth_helpers.py`
- Create: `backend/tests/test_auth_discovery.py`
- Create: `backend/tests/test_auth_validator.py`

**Interfaces:**
- Consumes: `AuthSettings`, `CurrentUser`, `InvalidBearerToken`, `IdentityProviderUnavailable` from Task 1.
- Produces: `OidcDiscoveryProvider.prewarm()`, `OidcDiscoveryProvider.get_signing_key(kid)`, and `JwtValidator.validate(token) -> CurrentUser`.

- [ ] **Step 1: Add deterministic RSA/JWK test helpers**

Create `backend/tests/auth_helpers.py` with one module-scoped RSA private key, a public JWK dictionary containing `kid="test-key"`, `make_access_token(**overrides)`, and these user fixtures:

```python
ADMIN = CurrentUser(sub="admin-sub", preferred_username="admin", roles=frozenset({AGENT_ADMIN}))
MANAGER = CurrentUser(sub="manager-sub", preferred_username="manager", roles=frozenset({AGENT_MANAGER}))
USER_A = CurrentUser(sub="user-a-sub", preferred_username="user-a", roles=frozenset({AGENT_USER}))
USER_B = CurrentUser(sub="user-b-sub", preferred_username="user-b", roles=frozenset({AGENT_USER}))
NO_ROLE = CurrentUser(sub="no-role-sub", preferred_username="no-role", roles=frozenset())
```

`make_access_token` must create a default payload with `iss`, `aud`, `sub`, `exp`, the two department claims, and roles under the exact resource-client path. It must accept `algorithm`, `kid`, `private_key`, and claim overrides so every rejection test changes only one property.

- [ ] **Step 2: Write failing discovery tests**

```python
def test_discovery_uses_discovered_jwks_uri_and_caches_key():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url).endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": "http://idp/keys"})
        return httpx.Response(200, json={"keys": [PUBLIC_JWK]})

    provider = OidcDiscoveryProvider(auth_settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    first = provider.get_signing_key("test-key")
    second = provider.get_signing_key("test-key")

    assert first.key_id == "test-key"
    assert second is first
    assert calls == [DISCOVERY_URL, "http://idp/keys"]


def test_unknown_kid_refreshes_once_then_rejects():
    provider, calls = build_provider_with_static_jwks([PUBLIC_JWK])

    with pytest.raises(InvalidBearerToken, match="signing key"):
        provider.get_signing_key("missing-key")

    assert calls.count("http://idp/keys") == 1


def test_discovery_issuer_mismatch_is_unavailable():
    provider = build_provider(discovery={"issuer": "http://attacker", "jwks_uri": "http://idp/keys"})

    with pytest.raises(IdentityProviderUnavailable, match="issuer"):
        provider.prewarm()
```

- [ ] **Step 3: Write failing validator tests**

Parameterize `test_auth_validator.py` for:

- valid token with array audience;
- valid token with string audience;
- expired token;
- wrong issuer;
- missing target audience;
- `HS256` or `none` algorithm rejection before key lookup;
- wrong RSA signature;
- missing or blank `sub`;
- roles present only under another client or realm role;
- correct admin/manager/user role extraction;
- department claim extraction.

The core success assertion is:

```python
user = validator.validate(make_access_token())
assert user.sub == "user-sub"
assert user.department_code == "IT"
assert user.roles == frozenset({"agent_user"})
```

- [ ] **Step 4: Run the focused tests and verify they fail**

Run: `cd backend && python -m pytest tests/test_auth_discovery.py tests/test_auth_validator.py -q`

Expected: collection fails because discovery and validator modules do not exist.

- [ ] **Step 5: Implement discovery and JWKS cache**

`backend/app/auth/discovery.py` must:

- own a thread-safe `dict[str, jwt.PyJWK]` cache;
- fetch discovery with `httpx.Client.get`;
- reject incomplete `AuthSettings` as `IdentityProviderUnavailable` before making a network call;
- require discovered `issuer == settings.issuer`;
- require an HTTP(S) `jwks_uri`;
- accept only RSA keys whose `alg` is absent or `RS256`;
- replace the cache atomically only after a complete successful refresh;
- return a cached key without network access;
- refresh once for an unknown `kid`, subject to a 30-second refresh cooldown;
- convert httpx, JSON, schema, and empty-key failures into `IdentityProviderUnavailable`;
- convert a still-unknown `kid` into `InvalidBearerToken`;
- never log token contents.

The concrete public API has three methods: the constructor stores `AuthSettings` and the injected `httpx.Client`; `prewarm() -> None` performs a forced refresh; `get_signing_key(kid: str) -> jwt.PyJWK` returns a cached key or performs the single allowed refresh before rejecting the token.

- [ ] **Step 6: Implement strict token validation**

`backend/app/auth/validator.py` must use a hard-coded/configured allow-list from `AuthSettings`, never the JWT header value, and call PyJWT as follows:

```python
claims = jwt.decode(
    token,
    key=signing_key,
    algorithms=list(self.settings.allowed_algorithms),
    audience=self.settings.audience,
    issuer=self.settings.issuer,
    options={"require": ["exp", "iss", "aud", "sub"]},
)
```

Before decoding, use `jwt.get_unverified_header` only to obtain `alg` and `kid`; reject missing `kid` or an algorithm outside `("RS256",)` before calling the provider. Map every `jwt.PyJWTError`, invalid claim shape, and empty `sub` to `InvalidBearerToken`.

Extract roles with an explicit shape check:

```python
resource_access = claims.get("resource_access")
client_access = resource_access.get(self.settings.resource_client_id) if isinstance(resource_access, dict) else None
raw_roles = client_access.get("roles") if isinstance(client_access, dict) else None
roles = frozenset(role for role in raw_roles if isinstance(role, str)) if isinstance(raw_roles, list) else frozenset()
```

- [ ] **Step 7: Run discovery and validator tests**

Run: `cd backend && python -m pytest tests/test_auth_discovery.py tests/test_auth_validator.py -q`

Expected: all tests pass.

- [ ] **Step 8: Commit the validation unit**

```bash
git add backend/app/auth/discovery.py backend/app/auth/validator.py backend/tests/auth_helpers.py backend/tests/test_auth_discovery.py backend/tests/test_auth_validator.py
git commit -m "Validate Keycloak access tokens"
```

---

### Task 3: FastAPI authentication runtime and role dependencies

**Files:**
- Create: `backend/app/auth/runtime.py`
- Create: `backend/app/auth/dependencies.py`
- Create: `backend/tests/test_auth_dependencies.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `OidcDiscoveryProvider` and `JwtValidator` from Task 2.
- Produces: `AuthRuntime`, `get_current_user`, `get_auth_runtime`, and `require_roles(*roles)` for Tasks 4–5.

- [ ] **Step 1: Write failing dependency tests with a minimal FastAPI app**

Create a two-route test app: `/me` depends on `get_current_user`; `/admin` depends on `require_roles(AGENT_ADMIN)`. Override `get_auth_runtime` with a fake runtime whose validator records tokens.

Assert:

```python
assert client.get("/me").status_code == 401
assert client.get("/me", headers={"Authorization": "Basic abc"}).status_code == 401
assert client.get("/me", headers={"Authorization": "Bearer valid"}).json()["sub"] == "user-a-sub"
assert client.get("/admin", headers={"Authorization": "Bearer user"}).status_code == 403
assert client.get("/admin", headers={"Authorization": "Bearer admin"}).status_code == 200
assert client.get("/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
assert client.get("/me", headers={"Authorization": "Bearer unavailable"}).status_code == 503
```

Verify each `401` includes `WWW-Authenticate: Bearer` and client responses never contain a raw token.

- [ ] **Step 2: Run the dependency tests and verify they fail**

Run: `cd backend && python -m pytest tests/test_auth_dependencies.py -q`

Expected: collection fails because `app.auth.dependencies` does not exist.

- [ ] **Step 3: Implement runtime ownership and lifecycle**

```python
# backend/app/auth/runtime.py
from __future__ import annotations

import httpx

from app.auth.discovery import OidcDiscoveryProvider
from app.auth.models import AuthSettings
from app.auth.validator import JwtValidator


class AuthRuntime:
    def __init__(self, settings: AuthSettings) -> None:
        self.client = httpx.Client(timeout=5.0, follow_redirects=False)
        self.provider = OidcDiscoveryProvider(settings, self.client)
        self.validator = JwtValidator(settings, self.provider)

    def prewarm(self) -> None:
        self.provider.prewarm()

    def close(self) -> None:
        self.client.close()
```

- [ ] **Step 4: Implement FastAPI dependencies**

`backend/app/auth/dependencies.py` must use `HTTPBearer(auto_error=False)` and return stable errors:

```python
def get_auth_runtime(request: Request) -> AuthRuntime:
    return request.app.state.auth_runtime


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    runtime: AuthRuntime = Depends(get_auth_runtime),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "需要有效的统一身份认证凭证。", headers={"WWW-Authenticate": "Bearer"})
    try:
        return runtime.validator.validate(credentials.credentials)
    except InvalidBearerToken as exc:
        raise HTTPException(401, "统一身份认证凭证无效或已过期。", headers={"WWW-Authenticate": "Bearer"}) from exc
    except IdentityProviderUnavailable as exc:
        raise HTTPException(503, "统一身份认证服务暂时不可用。") from exc


def require_roles(*allowed_roles: str):
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has_any_role(*allowed_roles):
            raise HTTPException(403, "当前用户没有执行此操作的权限。")
        return user

    return dependency
```

Instantiate `HTTPBearer` once at module scope, not per request.

- [ ] **Step 5: Attach runtime to application lifespan**

In `backend/app/main.py`, create `auth_runtime = AuthRuntime(settings.auth)` before the lifespan function, assign it to `app.state.auth_runtime` after app construction, call `prewarm()` during startup, catch only `IdentityProviderUnavailable` to log a degraded-auth warning, and call `auth_runtime.close()` during shutdown. Failure to prewarm must not enable anonymous access; dependencies continue returning `503` until a later refresh succeeds.

- [ ] **Step 6: Run dependency tests and backend syntax check**

Run: `cd backend && python -m pytest tests/test_auth_dependencies.py -q && python -m compileall app tests`

Expected: dependency tests pass and compileall reports no syntax errors.

- [ ] **Step 7: Commit the FastAPI auth runtime**

```bash
git add backend/app/auth/runtime.py backend/app/auth/dependencies.py backend/app/main.py backend/tests/test_auth_dependencies.py
git commit -m "Add FastAPI bearer authentication"
```

---

### Task 4: Persist immutable task ownership and centralize access policy

**Files:**
- Create: `backend/app/auth/policies.py`
- Create: `backend/tests/test_task_access_policy.py`
- Modify: `backend/app/models.py`
- Modify: `backend/tests/test_task_repository.py`

**Interfaces:**
- Consumes: `CurrentUser`, role constants.
- Produces: task owner fields, `TaskAccessPolicy.can_read`, `TaskAccessPolicy.can_mutate`, and `TaskAccessPolicy.filter_visible`.

- [ ] **Step 1: Write failing policy and persistence tests**

```python
def owned_task(task_id: str, owner_sub: str) -> CompareTask:
    return CompareTask(task_id=task_id, owner_sub=owner_sub)


def test_owner_can_read_and_mutate_own_task():
    task = owned_task("TA", USER_A.sub)
    assert TaskAccessPolicy().can_read(task, USER_A)
    assert TaskAccessPolicy().can_mutate(task, USER_A)


def test_other_user_and_manager_cannot_read_task():
    task = owned_task("TA", USER_A.sub)
    assert not TaskAccessPolicy().can_read(task, USER_B)
    assert not TaskAccessPolicy().can_read(task, MANAGER)


def test_admin_can_read_new_owned_task_but_not_legacy_task():
    policy = TaskAccessPolicy()
    assert policy.can_read(owned_task("TA", USER_A.sub), ADMIN)
    assert not policy.can_read(CompareTask(task_id="TLEGACY"), ADMIN)


def test_no_role_owner_can_read_but_cannot_mutate():
    task = owned_task("TN", NO_ROLE.sub)
    assert TaskAccessPolicy().can_read(task, NO_ROLE)
    assert not TaskAccessPolicy().can_mutate(task, NO_ROLE)
```

Add a repository round-trip test proving a legacy payload loads with empty owner fields. Add a test that assigning a different value to `task.owner_sub` raises a Pydantic frozen-field validation error.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && python -m pytest tests/test_task_access_policy.py tests/test_task_repository.py -q`

Expected: failures because owner fields and `TaskAccessPolicy` do not exist.

- [ ] **Step 3: Add owner fields to `CompareTask`**

Add directly after timestamps in `backend/app/models.py`:

```python
    owner_sub: str = Field(default="", frozen=True)
    owner_username: str = ""
    owner_display_name: str = ""
    owner_department_code: str = ""
    owner_department_name: str = ""
```

Keep defaults empty so old JSON remains loadable. Do not infer or backfill any owner during model validation or repository loading.

- [ ] **Step 4: Implement access policy**

```python
# backend/app/auth/policies.py
from __future__ import annotations

from app.auth.models import AGENT_ADMIN, APP_ROLES, CurrentUser
from app.models import CompareTask


class TaskAccessPolicy:
    def can_read(self, task: CompareTask, user: CurrentUser) -> bool:
        if not task.owner_sub:
            return False
        return user.has_any_role(AGENT_ADMIN) or task.owner_sub == user.sub

    def can_mutate(self, task: CompareTask, user: CurrentUser) -> bool:
        return self.can_read(task, user) and user.has_any_role(*APP_ROLES)

    def filter_visible(self, tasks: list[CompareTask], user: CurrentUser) -> list[CompareTask]:
        return [task for task in tasks if self.can_read(task, user)]
```

- [ ] **Step 5: Run policy and repository tests**

Run: `cd backend && python -m pytest tests/test_task_access_policy.py tests/test_task_repository.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit task ownership**

```bash
git add backend/app/auth/policies.py backend/app/models.py backend/tests/test_task_access_policy.py backend/tests/test_task_repository.py
git commit -m "Isolate comparison tasks by owner"
```

---

### Task 5: Protect every backend business endpoint

**Files:**
- Create: `backend/tests/test_api_authz.py`
- Modify: `backend/app/api.py`
- Modify: `backend/app/api_quality.py`
- Modify: `backend/app/application/compare_tasks.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/test_api_quality.py`

**Interfaces:**
- Consumes: `get_current_user`, `require_roles`, `TaskAccessPolicy`, and task owner fields.
- Produces: authenticated/authorized behavior for all business APIs while leaving route paths unchanged.

- [ ] **Step 1: Add an authenticated-admin test override for existing suites**

In `backend/tests/conftest.py`, add an autouse fixture that replaces `auth_runtime.prewarm` with a no-op, sets only `app.dependency_overrides[get_current_user] = lambda: ADMIN`, and removes only that dependency key in teardown. Do not call `dependency_overrides.clear()` and do not contact Keycloak from unit tests.

In `test_api_quality.py`, change the quality service fixture teardown from `app.dependency_overrides.clear()` to:

```python
app.dependency_overrides.pop(get_quality_workbench_service, None)
```

This preserves the auth override established by the autouse fixture.

- [ ] **Step 2: Write failing endpoint authorization matrix tests**

Use a helper that replaces the `get_current_user` override per request. Persist tasks owned by `USER_A`, `USER_B`, and one legacy task. Cover every row:

| Endpoint | USER_A own | USER_A other | ADMIN owned | ADMIN legacy | NO_ROLE own |
| --- | ---: | ---: | ---: | ---: | ---: |
| `GET /api/compare/records` | own only | own only | all owned | omitted | own only |
| `GET /api/compare/{id}` | 200 | 404 | 200 | 404 | 200 |
| `GET /progress` | 200 | 404 | 200 | 404 | 200 |
| `GET /diffs` | 200 | 404 | 200 | 404 | 200 |
| `GET /execution` | 200 with stub job | 404 | 200 with stub job | 404 | 200 with stub job |
| `GET /quality` | 200 | 404 | 200 | 404 | 200 |
| `GET /report` | 200 when complete | 404 | 200 | 404 | 200 |
| `GET /original` | 200 | 404 | 200 | 404 | 200 |
| `GET /compare` | 200 | 404 | 200 | 404 | 200 |
| `POST /cancel` | 200 with stub job | 404 | 200 with stub job | 404 | 403 |
| `POST /retry` | 200 with stub job | 404 | 200 with stub job | 404 | 403 |
| diff review PATCH | 200 | 404 | 200 | 404 | 403 |
| audit review PATCH | 200 | 404 | 200 | 404 | 403 |

Also assert all three application roles can `POST /api/compare`, the stored task contains all five owner snapshot fields from the authenticated principal, a no-role user gets `403`, and submitted `reviewed_by="forged"` is replaced by `current_user.sub`.

For execution, cancel, and retry rows, monkeypatch the corresponding application method to return a fixed `TaskJob` after authorization. Assert the method is not called for `404` or `403` cases.


- [ ] **Step 3: Run authorization tests and verify they fail**

Run: `cd backend && python -m pytest tests/test_api_authz.py -q`

Expected: unprotected routes return `200` where the test expects `403` or `404`.

- [ ] **Step 4: Add reusable API access helpers**

In `backend/app/api.py`, define module-level dependencies and policy:

```python
task_access_policy = TaskAccessPolicy()
require_app_role = require_roles(AGENT_ADMIN, AGENT_MANAGER, AGENT_USER)


def _load_accessible_or_404(task_id: str, user: CurrentUser, *, mutate: bool = False) -> CompareTask:
    task = _load_or_404(task_id)
    allowed = task_access_policy.can_mutate(task, user) if mutate else task_access_policy.can_read(task, user)
    if not allowed:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问。")
    return task
```

For mutation endpoints, enforce the role dependency before resource loading so a valid no-role owner receives `403`; still return `404` for another user's task by checking resource visibility with the role-qualified current user.

- [ ] **Step 5: Protect task creation and list filtering**

Add the keyword-only parameter `owner: CurrentUser` to `CompareTaskApplication.create_queued_task` after `compare_options` and populate:

```python
            owner_sub=owner.sub,
            owner_username=owner.preferred_username,
            owner_display_name=owner.display_name,
            owner_department_code=owner.department_code,
            owner_department_name=owner.department_name,
```

Do not place tokens or the complete claim dictionary in task metadata or background job payloads. Add `current_user: CurrentUser = Depends(require_app_role)` to `compare_contracts` and pass `owner=current_user` into `create_queued_task`.

Add `current_user: CurrentUser = Depends(get_current_user)` to `list_records` and replace the source task list with:

```python
tasks = task_access_policy.filter_visible(
    default_compare_task_application.list_compare_tasks(),
    current_user,
)
```

Apply date filtering and pagination after visibility filtering so counts do not leak other users' tasks.

- [ ] **Step 6: Protect every task subresource**

Add `current_user: CurrentUser = Depends(get_current_user)` and call `_load_accessible_or_404` before work in all read endpoints: task, progress, diffs, execution, quality, report, original, and compare.

Add `current_user: CurrentUser = Depends(require_app_role)` to cancel, retry, diff review, and audit review. Each function calls `_load_accessible_or_404(task_id, current_user, mutate=True)` before invoking its application operation.

For execution, cancel, and retry, authorize the persisted task before calling the application method that loads it again. For SSE, authorize before subscribing to `ProgressBus`. For file endpoints, authorize before resolving or testing the storage path.

Replace both review calls' last argument with `current_user.sub`; continue accepting the old request field for compatibility but never consume it.

- [ ] **Step 7: Protect the quality router**

Construct the router in `backend/app/api_quality.py` with:


Keep `/health` outside protected routers.

- [ ] **Step 8: Run authorization and existing API suites**

Run: `cd backend && python -m pytest tests/test_api_authz.py tests/test_api.py tests/test_api_quality.py -q`

Expected: all selected tests pass; existing route paths and response payload assertions remain unchanged.

- [ ] **Step 9: Commit backend endpoint protection**

```bash
git add backend/app/api.py backend/app/api_quality.py backend/app/application/compare_tasks.py backend/tests/conftest.py backend/tests/test_api.py backend/tests/test_api_quality.py backend/tests/test_api_authz.py
git commit -m "Protect contract comparison APIs"
```

---

### Task 6: Frontend OIDC bootstrap, callback, user model, and role-aware shell

**Files:**
- Create: `frontend/src/auth/config.ts`
- Create: `frontend/src/auth/config.test.ts`
- Create: `frontend/src/auth/userManager.ts`
- Create: `frontend/src/auth/currentUser.ts`
- Create: `frontend/src/auth/currentUser.test.ts`
- Create: `frontend/src/auth/AuthGate.tsx`
- Create: `frontend/src/auth/AuthGate.test.tsx`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/lib/state.tsx`
- Delete: `frontend/src/pages/LoginPage.tsx`
- Delete: `frontend/src/pages/LoginPage.test.tsx`

**Interfaces:**
- Produces: `getUserManager()`, `buildCurrentUser(accessToken, profile)`, `AuthGate`, and safe return-path helpers.
- Consumed by Tasks 7–8: `getUserManager()` for authenticated fetch and access-token retrieval.

- [ ] **Step 1: Install OIDC frontend dependencies**

Run: `cd frontend && npm install oidc-client-ts react-oidc-context`

Expected: package manifest and lockfile contain both runtime dependencies.

- [ ] **Step 2: Write failing OIDC config and user tests**

Test exact environment mapping, missing-variable errors, explicit `response_type="code"`, `disablePKCE=false`, both stores using `sessionStorage`, internal return path acceptance, and rejection of `https://evil.test`, `//evil.test`, and malformed state.

Create JWT test payloads by base64url-encoding JSON; do not add a JWT decoding dependency. Assert roles are read from the access token's exact resource-client path and ignore `realm_access.roles` and other clients.

- [ ] **Step 3: Implement OIDC configuration and lazy manager**

`frontend/src/auth/config.ts` must produce:

```ts
export interface AppOidcConfig {
  authority: string;
  client_id: string;
  redirect_uri: string;
  post_logout_redirect_uri: string;
  scope: string;
  response_type: "code";
  disablePKCE: false;
  automaticSilentRenew: true;
  maxSilentRenewTimeoutRetries: number;
}
```

Read `VITE_OIDC_AUTHORITY`, `VITE_OIDC_CLIENT_ID`, `VITE_OIDC_REDIRECT_URI`, `VITE_OIDC_POST_LOGOUT_REDIRECT_URI`, and `VITE_OIDC_SCOPE`; throw one configuration error listing missing variable names. Never read a Client Secret variable.

`getOidcConfig()` sets `response_type: "code"`, `disablePKCE: false`, `automaticSilentRenew: true`, and `maxSilentRenewTimeoutRetries: 1` exactly.

Export `REAUTH_ATTEMPT_KEY = "rixin_oidc_reauth_attempt"`. A successful sign-in callback and the AuthGate manual retry action both remove this key from `sessionStorage`; Task 7 uses the same constant to bound automatic 401 recovery.

`frontend/src/auth/userManager.ts` must lazily create exactly one manager:

```ts
let userManager: UserManager | undefined;

export function getUserManager(): UserManager {
  if (!userManager) {
    const store = new WebStorageStateStore({ store: window.sessionStorage });
    userManager = new UserManager({
      ...getOidcConfig(),
      userStore: store,
      stateStore: store,
      loadUserInfo: false,
    });
  }
  return userManager;
}
```

Use a separate `WebStorageStateStore` instance for each property if the installed type definitions reject sharing; both must wrap the same `window.sessionStorage`.

- [ ] **Step 4: Implement display-only current-user mapping**

Define:

```ts
export interface CurrentUser {
  sub: string;
  username: string;
  displayName: string;
  email: string;
  departmentCode: string;
  departmentName: string;
  roles: ReadonlySet<string>;
}
```

Decode the access-token payload by base64url-normalizing into bytes, decoding with `TextDecoder("utf-8")`, and then calling `JSON.parse`; the UTF-8 path must preserve Chinese department names. Build fields from access-token claims first and OIDC profile second. Throw if `sub` is empty. Export `hasRole(user, role)` and document in code that this decoding is for display only; the backend is authoritative.

- [ ] **Step 5: Write failing AuthGate tests**

Mock `useAuth` and assert:

- loading and active navigation render status screens;
- callback parameters do not trigger a second redirect;
- an unauthenticated stable state calls `signinRedirect` exactly once with `{state: {returnTo}}`;
- an auth error renders a manual retry button and does not auto-loop;
- callback state restores only a safe internal path and removes `code`/`state` from the URL;
- authenticated state renders children.

- [ ] **Step 6: Implement AuthGate and provider wiring**

`AuthGate` uses `hasAuthParams()`, one `useRef(false)` redirect guard, and `useAuth()`. Its retry button resets the local guard and calls `signinRedirect` with the current internal location.

In `main.tsx`, use:

```tsx
const manager = getUserManager();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider userManager={manager} onSigninCallback={handleSigninCallback}>
      <AuthGate>
        <AppProvider>
          <App />
        </AppProvider>
      </AuthGate>
    </AuthProvider>
  </StrictMode>,
);
```

`handleSigninCallback` reads `user?.state`, validates `returnTo`, and calls `history.replaceState` with the safe internal path.

Wrap initial manager construction so a missing OIDC environment variable renders a configuration error screen instead of leaving a blank root node. The retry action re-evaluates configuration; it must not fall back to the deleted local login.

- [ ] **Step 7: Remove fake auth state and make App role-aware**

Delete `currentUser`, `LOGIN`, `LOGOUT`, and `AUTH_STORAGE_KEY` from `lib/state.tsx`; keep only navigation/sidebar state.

In `App.tsx`, obtain `auth.user`, call `buildCurrentUser`, display `currentUser.displayName`, department, and avatar, and call `auth.signoutRedirect()` on logout.


- [ ] **Step 8: Run frontend auth and shell tests**

Run: `cd frontend && npm test -- src/auth src/App.test.tsx`

Expected: auth and App tests pass.

- [ ] **Step 9: Commit frontend OIDC bootstrap**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/auth frontend/src/main.tsx frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/lib/state.tsx frontend/src/pages/LoginPage.tsx frontend/src/pages/LoginPage.test.tsx
git commit -m "Replace local login with Keycloak OIDC"
```

---

### Task 7: Centralized authenticated HTTP and protected downloads

**Files:**
- Create: `frontend/src/lib/authFetch.ts`
- Create: `frontend/src/lib/authFetch.test.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/pages/ResultPage.tsx`
- Modify: `frontend/src/pages/ResultPage.test.tsx`
- Modify: `frontend/src/pages/ComparisonRecordsPage.tsx`
- Modify: `frontend/src/pages/ComparisonRecordsPage.test.tsx`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Consumes: `getUserManager()` from Task 6.
- Produces: `authorizedFetch`, `ApiError`, `downloadAuthenticatedFile`.

- [ ] **Step 1: Write failing authenticated-fetch tests**

Mock `getUserManager()` and assert:

```ts
expect(fetchMock).toHaveBeenCalledWith(url, expect.objectContaining({
  headers: expect.objectContaining({ Authorization: "Bearer access-token" }),
}));
```

Cover header merging, FormData without forced Content-Type, a missing session, an expired session renewed once with `signinSilent`, one 401-triggered `removeUser` + `signinRedirect`, concurrent 401 responses sharing one redirect, a second automatic reauthentication attempt being blocked, and stable mappings:

```text
403 -> 当前用户没有执行此操作的权限。
404 -> 任务不存在或无权访问。
503 -> 统一身份认证服务暂时不可用。
```

Test Blob download creates and revokes one object URL and uses the provided filename.

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd frontend && npm test -- src/lib/authFetch.test.ts`

Expected: module-not-found failure.

- [ ] **Step 3: Implement the authenticated HTTP layer**

`authorizedFetch(input, init)` must await `getUserManager().getUser()`. If the user is expired, call `signinSilent()` once and use the renewed user; if renewal fails or still has no usable token, begin the bounded reauthentication flow. Clone headers with `new Headers(init?.headers)`, set Authorization, and call `fetch`.

On `401`, use a module-scoped `reauthenticationPromise` to ensure concurrent failures cause one `removeUser()` and one `signinRedirect({state: {returnTo}})`. Before redirecting, store a numeric `rixin_oidc_reauth_attempt` counter in `sessionStorage`; one automatic attempt is allowed, and a second 401 renders the error without another redirect. Clear the counter after a successful sign-in callback or a manual retry. Throw `ApiError` after starting reauthentication so page code stops processing the response.

`ApiError` must expose `status: number`. `downloadAuthenticatedFile` must use `authorizedFetch`, validate `response.ok`, create an object URL, click a temporary anchor, and revoke the URL in `finally`.

- [ ] **Step 4: Route every API function through `authorizedFetch`**

Replace every direct `fetch` in `frontend/src/lib/api.ts` with `authorizedFetch`, including multipart upload and all quality-workbench mutations. Keep `toApiUrl` behavior unchanged.

Update `parseJsonResponse` to prefer a backend `detail`, otherwise use the stable status mapping. Remove `reviewed_by` from `DiffReviewPayload` in `types.ts`; callers send only review status and comment.

- [ ] **Step 5: Replace report and PDF file direct requests**

In `ResultPage.tsx`, remove its local raw-fetch download helper and import `downloadAuthenticatedFile`. Remove both hard-coded `reviewed_by: "local_reviewer"` values.

In `ComparisonRecordsPage.tsx`, replace the report `<a>` with a button that calls:

```ts
void downloadAuthenticatedFile(
  toApiUrl(record.report_url),
  `合同差异分析报告-${record.task_id}.pdf`,
).catch((error) => setError(error instanceof Error ? error.message : "报告下载失败。"));
```

This prevents a browser navigation without an Authorization Header.

- [ ] **Step 6: Update API, result, and records tests**

Mock `authorizedFetch` or the user manager, not the global fetch path alone. Assert uploads, record reads, reviews, quality calls, and downloads all pass through the auth layer and no payload contains `reviewed_by`.

- [ ] **Step 7: Run HTTP and page tests**

Run: `cd frontend && npm test -- src/lib/authFetch.test.ts src/lib/api.test.ts src/pages/ResultPage.test.tsx src/pages/ComparisonRecordsPage.test.tsx`

Expected: all selected tests pass.

- [ ] **Step 8: Commit authenticated HTTP changes**

```bash
git add frontend/src/lib/authFetch.ts frontend/src/lib/authFetch.test.ts frontend/src/lib/api.ts frontend/src/lib/api.test.ts frontend/src/pages/ResultPage.tsx frontend/src/pages/ResultPage.test.tsx frontend/src/pages/ComparisonRecordsPage.tsx frontend/src/pages/ComparisonRecordsPage.test.tsx frontend/src/types.ts
git commit -m "Authenticate frontend API requests"
```

---

### Task 8: Authenticated SSE and PDF.js transport

**Files:**
- Create: `frontend/src/lib/api_sse.test.ts`
- Modify: `frontend/src/lib/api_sse.ts`
- Modify: `frontend/src/lib/hooks.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/components/PdfDocumentViewer.tsx`
- Modify: `frontend/src/components/PdfDocumentViewerLoad.test.tsx`
- Modify: `frontend/src/pages/ResultPage.tsx`
- Modify: `frontend/src/pages/ResultPage.test.tsx`
- Modify: `frontend/src/pages/ComparisonRecordsPage.test.tsx`

**Interfaces:**
- Consumes: `authorizedFetch` and the `accessToken` prop produced by App.
- Produces: `ProgressEventStream` compatible with current hook lifecycle and PDF.js authenticated loads.

- [ ] **Step 1: Write failing SSE parser tests**

Mock `authorizedFetch` with a `ReadableStream<Uint8Array>` response split across arbitrary chunk boundaries. Assert:

- request URL is `/api/compare/{taskId}/progress`;
- Authorization is delegated to `authorizedFetch`;
- comment keepalives are ignored;
- multiple `data:` lines are joined with `\n`;
- two events in one chunk both call `onmessage`;
- `close()` aborts the request and does not call `onerror`;
- an HTTP error or unexpected EOF calls `onerror` once.

- [ ] **Step 2: Implement Fetch/ReadableStream SSE**

Replace native `EventSource` with this interface:

```ts
export interface ProgressEventStream {
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: ((error: unknown) => void) | null;
  close(): void;
}
```

`createProgressEventSource(taskId)` returns immediately with mutable handlers and an AbortController, starts `authorizedFetch` asynchronously, decodes chunks with `TextDecoder`, splits complete events on blank lines, ignores `:` comments, joins all `data:` lines, and dispatches `MessageEvent<string>`. Flush the decoder at EOF. Track `closed` and `failed` flags so close/error callbacks fire at most once.

- [ ] **Step 3: Update hooks to the transport-neutral interface**

Change `connectionsRef` from `Map<string, EventSource>` to `Map<string, ProgressEventStream>`. Preserve existing completion animations, polling fallback, cleanup, and public hook signatures.

- [ ] **Step 4: Write failing PDF authorization test**

Render `PdfDocumentViewer` with `accessToken="pdf-token"`, mock a successful PDF.js loading task, and assert:

```ts
expect(getDocument).toHaveBeenCalledWith({
  url: "http://api.test/file.pdf",
  httpHeaders: { Authorization: "Bearer pdf-token" },
});
```

Also assert the viewer does not start a request when the token is empty.

- [ ] **Step 5: Add authenticated PDF.js parameters**

Add required `accessToken: string` to `PdfDocumentViewerProps` and replace `getDocument(src)` with:

```ts
const loadingTask = pdfjsLib.getDocument({
  url: src,
  httpHeaders: { Authorization: `Bearer ${accessToken}` },
});
```

Include `accessToken` in the load effect dependency list. Add required `accessToken: string` to `ResultPage`, pass `auth.user.access_token` from `App` to `ResultPage`, and pass that prop to both original and comparison viewers. Update App, ResultPage, and PDF-viewer test render helpers with `accessToken="test-token"`.

- [ ] **Step 6: Run SSE, hooks, PDF, result, and records tests**

Run: `cd frontend && npm test -- src/lib/api_sse.test.ts src/App.test.tsx src/components/PdfDocumentViewerLoad.test.tsx src/pages/ResultPage.test.tsx src/pages/ComparisonRecordsPage.test.tsx`

Expected: all selected tests pass; no native `EventSource` remains in production code.

- [ ] **Step 7: Commit authenticated streaming and PDF transport**

```bash
git add frontend/src/lib/api_sse.ts frontend/src/lib/api_sse.test.ts frontend/src/lib/hooks.ts frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/components/PdfDocumentViewer.tsx frontend/src/components/PdfDocumentViewerLoad.test.tsx frontend/src/pages/ResultPage.tsx frontend/src/pages/ResultPage.test.tsx frontend/src/pages/ComparisonRecordsPage.test.tsx
git commit -m "Authenticate PDF and progress streams"
```

---

### Task 9: Environment contract, documentation, and complete verification

**Files:**
- Modify: `.env.example`
- Modify: `frontend/.env.example`
- Modify: `frontend/src/vite-env.d.ts`
- Modify: `README.md`
- Modify: `frontend/Dockerfile`
- Modify: `docker-compose.yml`
- Test: complete backend and frontend suites

**Interfaces:**
- Consumes: every implementation interface from Tasks 1–8.
- Produces: reproducible local configuration, explicit Docker limitation, and final verification evidence.

- [ ] **Step 1: Add safe environment examples**

Add to the backend/root example without any Client Secret:

```dotenv
# Keycloak / OIDC resource-server validation
OIDC_DISCOVERY_URL=http://10.8.6.32:18080/realms/company-dev/.well-known/openid-configuration
OIDC_ISSUER=http://10.8.6.32:18080/realms/company-dev
OIDC_AUDIENCE=rixin-contract-comparison-api
OIDC_RESOURCE_CLIENT_ID=rixin-contract-comparison-api
OIDC_ALLOWED_ALGORITHMS=RS256
```

Add to `frontend/.env.example`:

```dotenv
VITE_OIDC_AUTHORITY=http://10.8.6.32:18080/realms/company-dev
VITE_OIDC_CLIENT_ID=rixin-contract-comparison-web
VITE_OIDC_REDIRECT_URI=http://127.0.0.1:5173/callback
VITE_OIDC_POST_LOGOUT_REDIRECT_URI=http://127.0.0.1:5173/
VITE_OIDC_SCOPE=openid profile email
```

Declare only these five `VITE_OIDC_*` values in `vite-env.d.ts`; do not declare a secret variable.

- [ ] **Step 2: Pass public OIDC values through Docker builds without inventing registered URLs**

Add matching `ARG`/`ENV` pairs to `frontend/Dockerfile` and build args to `docker-compose.yml`. Leave Docker OIDC URI values empty in the root example and document that the current Keycloak registration authorizes only `127.0.0.1:5173`; container deployment requires the authentication administrator to register the container's exact callback, logout URI, and origin first.

Do not silently reuse the `5173` callback for the `/contract` Docker deployment.

- [ ] **Step 3: Update README authentication instructions**

Document:

- `http://127.0.0.1:5173` is required; `localhost:5173` is not registered;
- automatic redirect and `/callback` behavior;
- three application roles and exact claim path;
- owner isolation and admin all-new-task access;
- historical tasks are hidden and return `404`;
- backend validates signature, issuer, expiration, audience, and roles;
- test users must come from the authentication administrator;
- exposed Client Secret must be rotated and is not needed by this Resource Server.

- [ ] **Step 4: Run backend focused security verification**

Run:

```bash
cd backend
python -m pytest tests/test_auth_models.py tests/test_auth_discovery.py tests/test_auth_validator.py tests/test_auth_dependencies.py tests/test_task_access_policy.py tests/test_api_authz.py -q
```

Expected: all security and isolation tests pass.

- [ ] **Step 5: Run the complete backend quality gates**

Run:

```bash
cd backend
python -m compileall app tests
python -m ruff check .
python -m pytest
```

Expected: syntax, lint, and the complete backend suite pass.

- [ ] **Step 6: Run the complete frontend quality gates**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: all Vitest tests and the production TypeScript/Vite build pass.

- [ ] **Step 7: Scan for prohibited auth artifacts**

Run:

```bash
rg -n "rixin_contract_auth_user|local_reviewer|VITE_.*SECRET|CLIENT_SECRET|client_secret" frontend backend .env.example README.md
```

Expected: no matches. References in the design document explaining that secrets are prohibited are outside this scan scope.

Run:

```bash
rg -n "new EventSource|new window\.EventSource|fetch\(" frontend/src --glob '*.ts' --glob '*.tsx'
```

Expected: no native EventSource construction; direct fetch remains only inside `authFetch.ts` and test mocks. Every production API call uses `authorizedFetch`.

- [ ] **Step 8: Perform Keycloak smoke tests when accounts are available**

Using admin, manager, user A, and user B accounts supplied through a secure channel, verify:


If test accounts are not yet available, record this smoke test as externally blocked; do not weaken validation or create local password bypasses.

- [ ] **Step 9: Commit configuration and documentation**

```bash
git add .env.example frontend/.env.example frontend/src/vite-env.d.ts README.md frontend/Dockerfile docker-compose.yml
git commit -m "Document Keycloak deployment configuration"
```

- [ ] **Step 10: Final diff review**

Run: `git status --short && git diff --check 7388561..HEAD`

Expected: only intentional implementation files are committed; unrelated pre-existing working-tree changes remain unstaged; diff check is clean.

---

## Official implementation references

- oidc-client-ts discovery, UserManager, session storage, and renewal: `https://authts.github.io/oidc-client-ts/`
- react-oidc-context provider, callback cleanup, protected API, and auto sign-in: `https://github.com/authts/react-oidc-context`
- PyJWT fixed algorithm, issuer, audience, expiration, and required-claim validation: `https://pyjwt.readthedocs.io/en/latest/api.html`
- PDF.js `getDocument({url, httpHeaders})`: `https://mozilla.github.io/pdf.js/api/draft/module-pdfjsLib.html`
