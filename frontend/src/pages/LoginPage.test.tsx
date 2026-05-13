import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LoginPage } from "./LoginPage";

describe("LoginPage", () => {
  it("submits hard-coded admin credentials", async () => {
    const user = userEvent.setup();
    const onLogin = vi.fn(() => true);
    render(<LoginPage onLogin={onLogin} />);

    expect(screen.getByText("合同规范管理 合作高效共赢")).toBeInTheDocument();
    expect(screen.getByText("严控风险.提升效率.保障合规.驱动价值")).toBeInTheDocument();
    await user.type(screen.getByLabelText("用户名"), "admin");
    await user.type(screen.getByLabelText("密码"), "123456");
    await user.click(screen.getByRole("button", { name: "登录系统" }));

    expect(onLogin).toHaveBeenCalledWith("admin", "123456");
  });

  it("shows an error when credentials are rejected", async () => {
    const user = userEvent.setup();
    render(<LoginPage onLogin={() => false} />);

    await user.click(screen.getByRole("button", { name: "登录系统" }));

    expect(screen.getByText("用户名或密码错误。")).toBeInTheDocument();
  });
});
