export interface AppOidcConfig {
  authority: string;
  client_id: string;
  redirect_uri: string;
  post_logout_redirect_uri: string;
  scope: string;
  response_type: "code";
  disablePKCE: false;
  automaticSilentRenew: true;
  maxSilentRenewTimeoutRetries: number;
}

const requiredNames = [
  "VITE_OIDC_AUTHORITY",
  "VITE_OIDC_CLIENT_ID",
  "VITE_OIDC_REDIRECT_URI",
  "VITE_OIDC_POST_LOGOUT_REDIRECT_URI",
  "VITE_OIDC_SCOPE",
] as const;

export const REAUTH_ATTEMPT_KEY = "rixin_oidc_reauth_attempt";

export function getOidcConfig(): AppOidcConfig {
  const values = Object.fromEntries(requiredNames.map((name) => [name, import.meta.env[name]?.trim() ?? ""]));
  const missing = requiredNames.filter((name) => !values[name]);
  if (missing.length > 0) {
    throw new Error(`缺少 OIDC 前端配置：${missing.join(", ")}`);
  }

  return {
    authority: values.VITE_OIDC_AUTHORITY,
    client_id: values.VITE_OIDC_CLIENT_ID,
    redirect_uri: values.VITE_OIDC_REDIRECT_URI,
    post_logout_redirect_uri: values.VITE_OIDC_POST_LOGOUT_REDIRECT_URI,
    scope: values.VITE_OIDC_SCOPE,
    response_type: "code",
    disablePKCE: false,
    automaticSilentRenew: true,
    maxSilentRenewTimeoutRetries: 1,
  };
}

export function currentInternalPath(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

export function safeReturnTo(state: unknown): string {
  const value = typeof state === "string" ? state : state && typeof state === "object" && "returnTo" in state
    ? (state as { returnTo?: unknown }).returnTo
    : undefined;
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";

  try {
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin) return "/";
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return "/";
  }
}
