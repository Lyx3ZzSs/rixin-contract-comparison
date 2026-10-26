from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.errors import IdentityProviderUnavailable, InvalidBearerToken
from app.auth.models import CurrentUser
from app.auth.runtime import AuthRuntime

_bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_runtime(request: Request) -> AuthRuntime:
    return request.app.state.auth_runtime


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    runtime: AuthRuntime = Depends(get_auth_runtime),
) -> CurrentUser:
    if not runtime.settings.is_oidc:
        return runtime.settings.disabled_user
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="需要有效的统一身份认证凭证。",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return runtime.validator.validate(credentials.credentials)
    except InvalidBearerToken as exc:
        raise HTTPException(
            status_code=401,
            detail="统一身份认证凭证无效或已过期。",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except IdentityProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail="统一身份认证服务暂时不可用。") from exc


def require_roles(*allowed_roles: str):
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has_any_role(*allowed_roles):
            raise HTTPException(status_code=403, detail="当前用户没有执行此操作的权限。")
        return user

    return dependency
