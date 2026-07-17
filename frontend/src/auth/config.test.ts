import { afterEach, describe, expect, it, vi } from "vitest";

import { getOidcConfig, safeReturnTo } from "./config";

const oidcEnv = {
  VITE_OIDC_AUTHORITY: "http://10.8.6.32:18080/realms/company-dev",
  VITE_OIDC_CLIENT_ID: "rixin-contract-comparison-web",
  VITE_OIDC_REDIRECT_URI: "http://127.0.0.1:5173/callback",
  VITE_OIDC_POST_LOGOUT_REDIRECT_URI: "http://127.0.0.1:5173/",
  VITE_OIDC_SCOPE: "openid profile email",
};

afterEach(() => vi.unstubAllEnvs());

describe("getOidcConfig", () => {
  it("maps the public Keycloak environment to authorization code plus PKCE", () => {
    for (const [name, value] of Object.entries(oidcEnv)) vi.stubEnv(name, value);

    expect(getOidcConfig()).toEqual({
      authority: oidcEnv.VITE_OIDC_AUTHORITY,
      client_id: oidcEnv.VITE_OIDC_CLIENT_ID,
      redirect_uri: oidcEnv.VITE_OIDC_REDIRECT_URI,
      post_logout_redirect_uri: oidcEnv.VITE_OIDC_POST_LOGOUT_REDIRECT_URI,
      scope: oidcEnv.VITE_OIDC_SCOPE,
      response_type: "code",
      disablePKCE: false,
      automaticSilentRenew: true,
      maxSilentRenewTimeoutRetries: 1,
    });
  });

  it("reports every missing OIDC environment variable", () => {
    expect(() => getOidcConfig()).toThrow(
      "VITE_OIDC_AUTHORITY, VITE_OIDC_CLIENT_ID, VITE_OIDC_REDIRECT_URI, VITE_OIDC_POST_LOGOUT_REDIRECT_URI, VITE_OIDC_SCOPE",
    );
  });

  it("does not inherit local OIDC values from the test process", () => {
    expect(() => getOidcConfig()).toThrow("缺少 OIDC 前端配置");
  });
});

describe("safeReturnTo", () => {
  it("accepts only internal application paths", () => {
    expect(safeReturnTo("/records?status=done")).toBe("/records?status=done");
    expect(safeReturnTo("https://evil.test")).toBe("/");
    expect(safeReturnTo("//evil.test")).toBe("/");
    expect(safeReturnTo({ returnTo: "/task/123" })).toBe("/task/123");
    expect(safeReturnTo({ returnTo: "bad" })).toBe("/");
  });
});
