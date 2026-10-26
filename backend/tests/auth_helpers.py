from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.models import AGENT_ADMIN, AGENT_MANAGER, AGENT_USER, AuthSettings, CurrentUser

ISSUER = "http://idp/realms/company"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUDIENCE = "rixin-contract-comparison-api"

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_JWK = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key()))
PUBLIC_JWK.update({"kid": "test-key", "use": "sig", "alg": "RS256"})

ADMIN = CurrentUser(sub="admin-sub", preferred_username="admin", roles=frozenset({AGENT_ADMIN}))
MANAGER = CurrentUser(sub="manager-sub", preferred_username="manager", roles=frozenset({AGENT_MANAGER}))
USER_A = CurrentUser(sub="user-a-sub", preferred_username="user-a", roles=frozenset({AGENT_USER}))
USER_B = CurrentUser(sub="user-b-sub", preferred_username="user-b", roles=frozenset({AGENT_USER}))
NO_ROLE = CurrentUser(sub="no-role-sub", preferred_username="no-role", roles=frozenset())


def auth_settings() -> AuthSettings:
    return AuthSettings(
        discovery_url=DISCOVERY_URL,
        issuer=ISSUER,
        audience=AUDIENCE,
        resource_client_id=AUDIENCE,
    )


def make_access_token(
    *,
    algorithm: str = "RS256",
    kid: str = "test-key",
    private_key: Any = PRIVATE_KEY,
    **overrides: Any,
) -> str:
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": [AUDIENCE, "account"],
        "sub": "user-sub",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "preferred_username": "user",
        "department_code": "IT",
        "department_name": "信息技术部",
        "resource_access": {AUDIENCE: {"roles": [AGENT_USER]}},
    }
    payload.update(overrides)
    key = None if algorithm == "none" else private_key
    return jwt.encode(payload, key=key, algorithm=algorithm, headers={"kid": kid})


@pytest.fixture
def rsa_private_key():
    return PRIVATE_KEY
