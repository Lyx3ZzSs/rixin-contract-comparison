from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

AGENT_ADMIN = "agent_admin"
AGENT_MANAGER = "agent_manager"
AGENT_USER = "agent_user"
APP_ROLES = frozenset({AGENT_ADMIN, AGENT_MANAGER, AGENT_USER})
AuthMode = Literal["oidc", "disabled"]


class AuthSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: AuthMode = "oidc"
    discovery_url: str = ""
    issuer: str = ""
    audience: str = ""
    resource_client_id: str = ""
    allowed_algorithms: tuple[str, ...] = ("RS256",)
    disabled_user_sub: str = "local-dev"
    disabled_user_name: str = "本地开发用户"
    disabled_user_roles: frozenset[str] = APP_ROLES

    @field_validator("discovery_url", "issuer")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if normalized and not normalized.startswith(("http://", "https://")):
            raise ValueError("OIDC URLs must start with http:// or https://")
        return normalized

    @field_validator("disabled_user_sub")
    @classmethod
    def validate_disabled_user_sub(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("AUTH_DISABLED_USER_SUB must not be empty")
        return normalized

    @field_validator("disabled_user_roles", mode="before")
    @classmethod
    def normalize_disabled_user_roles(cls, value: object) -> frozenset[str]:
        if isinstance(value, str):
            return frozenset(role.strip() for role in value.split(",") if role.strip())
        return frozenset(value)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def validate_disabled_user_roles(self) -> AuthSettings:
        unknown_roles = self.disabled_user_roles - APP_ROLES
        if unknown_roles:
            raise ValueError(f"AUTH_DISABLED_USER_ROLES contains unsupported roles: {', '.join(sorted(unknown_roles))}")
        if self.mode == "disabled" and not self.disabled_user_roles:
            raise ValueError("AUTH_DISABLED_USER_ROLES must not be empty when AUTH_MODE=disabled")
        return self

    @property
    def is_configured(self) -> bool:
        return bool(self.discovery_url and self.issuer and self.audience and self.resource_client_id)

    @property
    def is_oidc(self) -> bool:
        return self.mode == "oidc"

    @property
    def disabled_user(self) -> CurrentUser:
        return CurrentUser(
            sub=self.disabled_user_sub,
            preferred_username=self.disabled_user_sub,
            name=self.disabled_user_name.strip(),
            roles=self.disabled_user_roles,
        )


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
