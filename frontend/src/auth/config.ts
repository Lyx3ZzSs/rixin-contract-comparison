import type { CurrentUser } from "./currentUser";

export type AuthMode = "oidc" | "disabled";

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

export function getAuthMode(): AuthMode {
  const mode = (import.meta.env.VITE_AUTH_MODE?.trim().toLowerCase() || "oidc");
  if (mode !== "oidc" && mode !== "disabled") {
    throw new Error("VITE_AUTH_MODE 必须是 oidc 或 disabled。");
  }
  return mode;
}

export function getDisabledCurrentUser(): CurrentUser {
  const sub = import.meta.env.VITE_AUTH_DISABLED_USER_SUB?.trim() || "local-dev";
  const displayName = import.meta.env.VITE_AUTH_DISABLED_USER_NAME?.trim() || "本地开发用户";
  const rawRoles = import.meta.env.VITE_AUTH_DISABLED_USER_ROLES?.trim()
    || "agent_admin,agent_manager,agent_user";
  const roles = new Set(rawRoles.split(",").map((role) => role.trim()).filter(Boolean));
  const supportedRoles = new Set(["agent_admin", "agent_manager", "agent_user"]);
  const unknownRoles = [...roles].filter((role) => !supportedRoles.has(role));
  if (unknownRoles.length > 0) {
    throw new Error(`VITE_AUTH_DISABLED_USER_ROLES 包含不支持的角色：${unknownRoles.join(", ")}`);
  }
  if (roles.size === 0) {
    throw new Error("VITE_AUTH_DISABLED_USER_ROLES 在 disabled 模式下不能为空。");
  }
  return {
    sub,
    username: sub,
    displayName,
    email: "",
    departmentCode: "",
    departmentName: "本地模式",
    roles,
  };
}

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
