import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { AppProvider } from "./lib/state";

vi.mock("./pages/UploadPage", () => ({ UploadPage: () => <div>Mock Upload Page</div> }));
vi.mock("./pages/ComparisonRecordsPage", () => ({ ComparisonRecordsPage: () => <div>Mock Records Page</div> }));
vi.mock("./pages/ResultPage", () => ({ ResultPage: () => <div>Mock Result Page</div> }));
vi.mock("./pages/QualityWorkbenchPage", () => ({ QualityWorkbenchPage: () => <div>Mock Quality Workbench</div> }));

const signoutRedirect = vi.fn().mockResolvedValue(undefined);
let authUser: { access_token: string; profile: Record<string, unknown> };

vi.mock("react-oidc-context", () => ({
  useAuth: () => ({ user: authUser, signoutRedirect }),
}));

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
  authUser = { access_token: token(roles), profile: {} };
  window.history.pushState({}, "", pathname);
  render(
    <AppProvider>
      <App />
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
  it("renders the quality workbench for an administrator", () => {
    renderAuthenticatedApp("/quality/workbench", ["agent_admin"]);
    expect(screen.getByText("Mock Quality Workbench")).toBeInTheDocument();
  });

  it("denies the quality workbench direct route to a non-admin", () => {
    renderAuthenticatedApp("/quality/workbench");
    expect(screen.getByText("当前用户没有访问质量工作台的权限。")).toBeInTheDocument();
    expect(screen.queryByText("Mock Quality Workbench")).not.toBeInTheDocument();
  });

  it("shows the quality menu only to an administrator and logs out through OIDC", async () => {
    const user = userEvent.setup();
    renderAuthenticatedApp("/", ["agent_admin"]);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    expect(screen.getByRole("button", { name: /质量工作台/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(signoutRedirect).toHaveBeenCalledTimes(1);
  });

  it("does not write the removed local auth storage key", async () => {
    const user = userEvent.setup();
    renderAuthenticatedApp("/");
    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    expect(window.localStorage.getItem("rixin_contract_auth_user")).toBeNull();
  });
});
