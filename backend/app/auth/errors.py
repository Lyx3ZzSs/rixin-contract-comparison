class InvalidBearerToken(Exception):
    """The presented bearer token cannot be trusted."""


class IdentityProviderUnavailable(Exception):
    """OIDC metadata or signing keys are temporarily unavailable."""
