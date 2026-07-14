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
