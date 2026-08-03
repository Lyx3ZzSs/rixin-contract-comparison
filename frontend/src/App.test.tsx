import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { AppProvider } from "./lib/state";

vi.mock("./pages/UploadPage", () => ({ UploadPage: () => <div>Mock Upload Page</div> }));
vi.mock("./pages/ComparisonRecordsPage", () => ({ ComparisonRecordsPage: () => <div>Mock Records Page</div> }));
vi.mock("./pages/ResultPage", () => ({ ResultPage: () => <div>Mock Result Page</div> }));

const signoutRedirect = vi.fn().mockResolvedValue(undefined);

function token(roles: string[]): string {
  const payload = btoa(unescape(encodeURIComponent(JSON.stringify({
    sub: "keycloak-user",
    preferred_username: "tester",
    name: "测试用户",
    department_name: "信息技术部",
    resource_access: { "rixin-contract-comparison-api": { roles } },
  })))).replace(/=/g, "");
  return `header.${payload}.signature`;
}

function renderAuthenticatedApp(pathname = "/", roles = ["agent_user"]) {
  window.history.pushState({}, "", pathname);
  render(
    <AppProvider>
      <App
        currentUser={{
          sub: "keycloak-user",
          username: "tester",
          displayName: "测试用户",
          email: "",
          departmentCode: "",
          departmentName: "信息技术部",
          roles: new Set(roles),
        }}
        accessToken={token(roles)}
        onSignOut={() => void signoutRedirect()}
      />
    </AppProvider>,
  );
}

afterEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.pushState({}, "", "/");
  signoutRedirect.mockClear();
  vi.clearAllMocks();
});

describe("App", () => {
  it("routes the retired quality workbench path to home", () => {
    renderAuthenticatedApp("/quality/workbench", ["agent_admin"]);
    expect(screen.getByText("Mock Upload Page")).toBeInTheDocument();
  });

  it("logs out through OIDC", async () => {
    const user = userEvent.setup();
    renderAuthenticatedApp("/", ["agent_admin"]);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    expect(screen.queryByRole("button", { name: /质量工作台/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(signoutRedirect).toHaveBeenCalledTimes(1);
  });

  it("does not write the removed local auth storage key", async () => {
    const user = userEvent.setup();
    renderAuthenticatedApp("/");
    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    expect(window.localStorage.length).toBe(0);
  });

  it("hides logout when authentication is disabled", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/");
    render(
      <AppProvider>
        <App
          currentUser={{
            sub: "local-dev",
            username: "local-dev",
            displayName: "本地开发用户",
            email: "",
            departmentCode: "",
            departmentName: "本地模式",
            roles: new Set(["agent_admin"]),
          }}
        />
      </AppProvider>,
    );
    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    expect(screen.queryByRole("button", { name: "退出登录" })).not.toBeInTheDocument();
  });
});
