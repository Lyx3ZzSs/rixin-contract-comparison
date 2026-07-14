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
