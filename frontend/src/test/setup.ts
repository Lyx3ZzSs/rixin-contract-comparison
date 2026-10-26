import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, vi } from "vitest";

const oidcEnvironmentVariables = [
  "VITE_AUTH_MODE",
  "VITE_AUTH_DISABLED_USER_SUB",
  "VITE_AUTH_DISABLED_USER_NAME",
  "VITE_AUTH_DISABLED_USER_ROLES",
  "VITE_OIDC_AUTHORITY",
  "VITE_OIDC_CLIENT_ID",
  "VITE_OIDC_REDIRECT_URI",
  "VITE_OIDC_POST_LOGOUT_REDIRECT_URI",
  "VITE_OIDC_SCOPE",
] as const;

beforeEach(() => {
  for (const name of oidcEnvironmentVariables) vi.stubEnv(name, "");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

class TestDOMMatrix {
  a = 1;
  b = 0;
  c = 0;
  d = 1;
  e = 0;
  f = 0;

  translateSelf() {
    return this;
  }

  scaleSelf() {
    return this;
  }

  multiplySelf() {
    return this;
  }
}

if (!("DOMMatrix" in globalThis)) {
  Object.defineProperty(globalThis, "DOMMatrix", {
    value: TestDOMMatrix,
    writable: true,
  });
}
