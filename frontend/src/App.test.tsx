import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  it("shows the signed-in user and logs out from the expanded sidebar", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));

    expect(screen.getByLabelText("当前用户")).toHaveTextContent("admin");
    await user.click(screen.getByRole("button", { name: "退出登录" }));

    expect(window.localStorage.getItem("rixin_contract_auth_user")).toBeNull();
    expect(screen.getByRole("button", { name: "登录系统" })).toBeInTheDocument();
  });
});
