from __future__ import annotations

from typing import Any, Protocol

import jwt
from pydantic import ValidationError

from app.auth.errors import IdentityProviderUnavailable, InvalidBearerToken
from app.auth.models import AuthSettings, CurrentUser


class SigningKeyProvider(Protocol):
    def get_signing_key(self, kid: str) -> jwt.PyJWK: ...


class JwtValidator:
    def __init__(self, settings: AuthSettings, signing_keys: SigningKeyProvider) -> None:
        self.settings = settings
        self.signing_keys = signing_keys

    def validate(self, token: str) -> CurrentUser:
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            kid = header.get("kid")
            if algorithm != "RS256" or algorithm not in self.settings.allowed_algorithms:
                raise InvalidBearerToken("token signing algorithm is not allowed")
            if not isinstance(kid, str) or not kid.strip():
                raise InvalidBearerToken("token is missing a signing key identifier")

            signing_key = self.signing_keys.get_signing_key(kid)
            claims = jwt.decode(
                token,
                key=signing_key,
                algorithms=list(self.settings.allowed_algorithms),
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
            if not isinstance(claims, dict):
                raise InvalidBearerToken("token claims have an invalid shape")
            return self._to_current_user(claims)
        except (InvalidBearerToken, IdentityProviderUnavailable):
            raise
        except (jwt.PyJWTError, ValidationError, TypeError, ValueError) as exc:
            raise InvalidBearerToken("token validation failed") from exc

    def _to_current_user(self, claims: dict[str, Any]) -> CurrentUser:
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise InvalidBearerToken("token subject is missing")

        resource_access = claims.get("resource_access")
        client_access = resource_access.get(self.settings.resource_client_id) if isinstance(resource_access, dict) else None
        raw_roles = client_access.get("roles") if isinstance(client_access, dict) else None
        roles = frozenset(role for role in raw_roles if isinstance(role, str)) if isinstance(raw_roles, list) else frozenset()

        return CurrentUser(
            sub=sub,
            preferred_username=self._string_claim(claims, "preferred_username"),
            name=self._string_claim(claims, "name"),
            email=self._string_claim(claims, "email"),
            department_code=self._string_claim(claims, "department_code"),
            department_name=self._string_claim(claims, "department_name"),
            roles=roles,
        )

    @staticmethod
    def _string_claim(claims: dict[str, Any], name: str) -> str:
        value = claims.get(name, "")
        return value if isinstance(value, str) else ""
