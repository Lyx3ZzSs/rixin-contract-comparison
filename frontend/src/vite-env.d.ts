/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AUTH_MODE?: string;
  readonly VITE_AUTH_DISABLED_USER_SUB?: string;
  readonly VITE_AUTH_DISABLED_USER_NAME?: string;
  readonly VITE_AUTH_DISABLED_USER_ROLES?: string;
  readonly VITE_OIDC_AUTHORITY?: string;
  readonly VITE_OIDC_CLIENT_ID?: string;
  readonly VITE_OIDC_REDIRECT_URI?: string;
  readonly VITE_OIDC_POST_LOGOUT_REDIRECT_URI?: string;
  readonly VITE_OIDC_SCOPE?: string;
}
