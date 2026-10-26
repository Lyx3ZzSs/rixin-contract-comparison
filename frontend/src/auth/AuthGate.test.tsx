import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "./AuthGate";

const signinRedirect = vi.fn().mockResolvedValue(undefined);
let authState: Record<string, unknown>;
let authParams = false;

vi.mock("react-oidc-context", () => ({
  hasAuthParams: () => authParams,
  useAuth: () => authState,
}));

function renderGate() {
  return render(
    <AuthGate>
      <div>secured content</div>
    </AuthGate>,
  );
}

afterEach(() => {
  authParams = false;
  authState = { isLoading: false, isAuthenticated: false, signinRedirect };
  signinRedirect.mockClear();
  window.history.pushState({}, "", "/");
  window.sessionStorage.clear();
});

describe("AuthGate", () => {
  it("renders status while loading or navigating", () => {
    authState = { isLoading: true, isAuthenticated: false, signinRedirect };
    renderGate();
    expect(screen.getByText("正在初始化统一身份认证…")).toBeInTheDocument();
  });

  it("does not redirect a callback a second time", () => {
    authParams = true;
    authState = { isLoading: false, isAuthenticated: false, signinRedirect };
    renderGate();
    expect(screen.getByText("正在完成统一身份认证…")).toBeInTheDocument();
    expect(signinRedirect).not.toHaveBeenCalled();
  });

  it("redirects an unauthenticated stable state once with the internal return path", async () => {
    window.history.pushState({}, "", "/records?page=2");
    authState = { isLoading: false, isAuthenticated: false, signinRedirect };
    renderGate();

    await waitFor(() => expect(signinRedirect).toHaveBeenCalledWith({ state: { returnTo: "/records?page=2" } }));
    expect(signinRedirect).toHaveBeenCalledTimes(1);
  });

  it("shows a manual retry after an authentication error without an automatic loop", async () => {
    authState = { isLoading: false, isAuthenticated: false, error: new Error("failed"), signinRedirect };
    const user = userEvent.setup();
    renderGate();

    expect(screen.getByText("统一身份认证失败。")).toBeInTheDocument();
    expect(signinRedirect).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "重新登录" }));
    expect(signinRedirect).toHaveBeenCalledTimes(1);
  });

  it("renders children once authenticated", () => {
    authState = { isLoading: false, isAuthenticated: true, signinRedirect };
    renderGate();
    expect(screen.getByText("secured content")).toBeInTheDocument();
  });
});
