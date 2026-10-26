from __future__ import annotations

import threading
import time
from typing import Any

import httpx
import jwt

from app.auth.errors import IdentityProviderUnavailable, InvalidBearerToken
from app.auth.models import AuthSettings

_UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS = 30.0


class OidcDiscoveryProvider:
    """Loads discovery metadata and caches Keycloak's RSA signing keys."""

    def __init__(self, settings: AuthSettings, client: httpx.Client) -> None:
        self.settings = settings
        self.client = client
        self._lock = threading.Lock()
        self._keys: dict[str, jwt.PyJWK] = {}
        self._last_refresh_monotonic: float | None = None

    def prewarm(self) -> None:
        with self._lock:
            self._refresh_locked()

    def get_signing_key(self, kid: str) -> jwt.PyJWK:
        if not kid:
            raise InvalidBearerToken("token is missing a signing key identifier")

        with self._lock:
            cached = self._keys.get(kid)
            if cached is not None:
                return cached

            now = time.monotonic()
            if self._last_refresh_monotonic is None or (
                now - self._last_refresh_monotonic >= _UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS
            ):
                self._refresh_locked()

            signing_key = self._keys.get(kid)
            if signing_key is None:
                raise InvalidBearerToken("token signing key is not recognized")
            return signing_key

    def _refresh_locked(self) -> None:
        if not self.settings.is_configured:
            raise IdentityProviderUnavailable("OIDC configuration is incomplete")

        try:
            discovery = self._get_json(self.settings.discovery_url)
            issuer = discovery.get("issuer")
            jwks_uri = discovery.get("jwks_uri")
            if issuer != self.settings.issuer:
                raise IdentityProviderUnavailable("OIDC discovery issuer does not match configuration")
            if not isinstance(jwks_uri, str) or not jwks_uri.startswith(("http://", "https://")):
                raise IdentityProviderUnavailable("OIDC discovery does not provide a valid jwks_uri")

            jwks = self._get_json(jwks_uri)
            raw_keys = jwks.get("keys")
            if not isinstance(raw_keys, list):
                raise IdentityProviderUnavailable("OIDC JWKS response does not contain keys")

            refreshed_keys: dict[str, jwt.PyJWK] = {}
            for raw_key in raw_keys:
                if not isinstance(raw_key, dict):
                    continue
                if raw_key.get("kty") != "RSA" or raw_key.get("alg") not in {None, "RS256"}:
                    continue
                key_id = raw_key.get("kid")
                if not isinstance(key_id, str) or not key_id:
                    continue
                try:
                    refreshed_keys[key_id] = jwt.PyJWK.from_dict(raw_key)
                except (jwt.PyJWTError, TypeError, ValueError):
                    continue

            if not refreshed_keys:
                raise IdentityProviderUnavailable("OIDC JWKS response has no usable RSA signing keys")
        except IdentityProviderUnavailable:
            raise
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise IdentityProviderUnavailable("unable to load OIDC discovery metadata or signing keys") from exc

        self._keys = refreshed_keys
        self._last_refresh_monotonic = time.monotonic()

    def _get_json(self, url: str) -> dict[str, Any]:
        response = self.client.get(url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("OIDC endpoint returned a non-object JSON payload")
        return payload
