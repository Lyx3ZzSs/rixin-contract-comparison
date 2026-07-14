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
