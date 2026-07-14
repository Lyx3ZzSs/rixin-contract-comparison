import { describe, expect, it } from "vitest";

import { buildCurrentUser, hasRole } from "./currentUser";

function accessToken(payload: object): string {
  const value = JSON.stringify(payload);
  const encoded = btoa(unescape(encodeURIComponent(value))).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `header.${encoded}.signature`;
}

describe("buildCurrentUser", () => {
  it("reads roles only from the API resource access claim and preserves Chinese claims", () => {
    const user = buildCurrentUser(
      accessToken({
        sub: "keycloak-id",
        preferred_username: "alice",
        name: "Alice",
        department_code: "IT",
        department_name: "信息技术部",
        realm_access: { roles: ["agent_admin"] },
        resource_access: {
          "other-client": { roles: ["agent_admin"] },
          "rixin-contract-comparison-api": { roles: ["agent_user"] },
        },
      }),
      { email: "alice@example.test" },
    );

    expect(user).toMatchObject({
      sub: "keycloak-id",
      username: "alice",
      displayName: "Alice",
      email: "alice@example.test",
      departmentCode: "IT",
      departmentName: "信息技术部",
    });
    expect(hasRole(user, "agent_user")).toBe(true);
    expect(hasRole(user, "agent_admin")).toBe(false);
  });

  it("requires a non-empty access-token sub", () => {
    expect(() => buildCurrentUser(accessToken({ sub: " " }), {})).toThrow("sub");
  });
});
