from __future__ import annotations

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.errors import InvalidBearerToken

from auth_helpers import AUDIENCE, PRIVATE_KEY, PUBLIC_JWK, auth_settings, make_access_token


class StaticProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.key = jwt.PyJWK.from_dict(PUBLIC_JWK)

    def get_signing_key(self, kid: str) -> jwt.PyJWK:
        self.calls.append(kid)
        return self.key


def validator_with_provider() -> tuple[object, StaticProvider]:
    from app.auth.validator import JwtValidator

    provider = StaticProvider()
    return JwtValidator(auth_settings(), provider), provider


@pytest.mark.parametrize("audience", [[AUDIENCE, "account"], AUDIENCE])
def test_accepts_valid_tokens_with_array_or_string_audience(audience) -> None:
    validator, _ = validator_with_provider()

    user = validator.validate(make_access_token(aud=audience))

    assert user.sub == "user-sub"
    assert user.department_code == "IT"
    assert user.roles == frozenset({"agent_user"})


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 0},
        {"iss": "http://attacker/realms/company"},
        {"aud": ["other-api"]},
        {"sub": ""},
        {"sub": "   "},
    ],
)
def test_rejects_invalid_required_claims(claims) -> None:
    validator, _ = validator_with_provider()

    with pytest.raises(InvalidBearerToken):
        validator.validate(make_access_token(**claims))


@pytest.mark.parametrize("algorithm", ["HS256", "none"])
def test_rejects_non_rs256_before_signing_key_lookup(algorithm: str) -> None:
    validator, provider = validator_with_provider()
    key = "shared-secret-that-is-long-enough-for-hs256" if algorithm == "HS256" else PRIVATE_KEY

    with pytest.raises(InvalidBearerToken):
        validator.validate(make_access_token(algorithm=algorithm, private_key=key))

    assert provider.calls == []


def test_rejects_wrong_rsa_signature() -> None:
    validator, _ = validator_with_provider()
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(InvalidBearerToken):
        validator.validate(make_access_token(private_key=attacker_key))


def test_ignores_realm_and_other_client_roles() -> None:
    validator, _ = validator_with_provider()
    token = make_access_token(
        realm_access={"roles": ["agent_admin"]},
        resource_access={"other-client": {"roles": ["agent_admin"]}},
    )

    assert validator.validate(token).roles == frozenset()


@pytest.mark.parametrize("role", ["agent_admin", "agent_manager", "agent_user"])
def test_extracts_roles_only_from_resource_client(role: str) -> None:
    validator, _ = validator_with_provider()
    token = make_access_token(resource_access={AUDIENCE: {"roles": [role]}})

    assert validator.validate(token).roles == frozenset({role})


def test_extracts_department_claims() -> None:
    validator, _ = validator_with_provider()
    user = validator.validate(make_access_token(department_code="MGT", department_name="公司管理层"))

    assert user.department_code == "MGT"
    assert user.department_name == "公司管理层"
