from __future__ import annotations

import httpx
import pytest

from app.auth.errors import IdentityProviderUnavailable, InvalidBearerToken

from auth_helpers import DISCOVERY_URL, ISSUER, PUBLIC_JWK, auth_settings


def test_discovery_uses_discovered_jwks_uri_and_caches_key() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": "http://idp/keys"})
        return httpx.Response(200, json={"keys": [PUBLIC_JWK]})

    from app.auth.discovery import OidcDiscoveryProvider

    provider = OidcDiscoveryProvider(auth_settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    first = provider.get_signing_key("test-key")
    second = provider.get_signing_key("test-key")

    assert first.key_id == "test-key"
    assert second is first
    assert calls == [DISCOVERY_URL, "http://idp/keys"]


def test_unknown_kid_refreshes_once_then_rejects() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": "http://idp/keys"})
        return httpx.Response(200, json={"keys": [PUBLIC_JWK]})

    from app.auth.discovery import OidcDiscoveryProvider

    provider = OidcDiscoveryProvider(auth_settings(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(InvalidBearerToken, match="signing key"):
        provider.get_signing_key("missing-key")

    assert calls.count("http://idp/keys") == 1


def test_discovery_issuer_mismatch_is_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": "http://attacker", "jwks_uri": "http://idp/keys"})

    from app.auth.discovery import OidcDiscoveryProvider

    provider = OidcDiscoveryProvider(auth_settings(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(IdentityProviderUnavailable, match="issuer"):
        provider.prewarm()
