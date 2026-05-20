import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

vi.mock("./components/PdfDocumentViewer", () => ({
  PdfDocumentViewer: () => null,
}));

vi.mock("./pages/UploadPage", () => ({
  UploadPage: ({ onTaskCreated }: { onTaskCreated: (taskId: string) => void }) => (
    <button type="button" onClick={() => onTaskCreated("task-2")}>
      创建任务
    </button>
  ),
}));

vi.mock("./pages/ResultPage", () => ({
  ResultPage: ({ taskId, onBack }: { taskId: string; onBack: () => void }) => (
    <section>
      <h1>对比结果 {taskId}</h1>
      <button type="button" onClick={onBack}>
        返回新建对比
      </button>
    </section>
  ),
}));

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

  it("shows comparison records inside the expanded contract dropdown", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");
    window.localStorage.setItem(
      "rixin_contract_compare_history",
      JSON.stringify([{ taskId: "task-1", createdAt: "2026-05-20T10:30:00.000Z" }]),
    );

    render(<App />);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));

    expect(screen.getByRole("button", { name: "合同智能对比" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "合同对比" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "对比记录" })).toBeInTheDocument();
    expect(screen.getByLabelText("合同智能对比菜单")).toHaveTextContent("对比记录");
    expect(screen.queryByText("暂无对比记录")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /task-1/ })).toBeInTheDocument();
  });

  it("shows extraction tools as a sibling dropdown", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));

    expect(screen.getByRole("button", { name: "合同智能提取" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("合同智能提取菜单")).toHaveTextContent("合同提取");
    expect(screen.getByRole("button", { name: "合同提取" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提取记录" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提取字段管理" })).toBeInTheDocument();
  });

  it("opens the contract extraction page from the extraction menu", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    await user.click(screen.getByRole("button", { name: "合同提取" }));

    expect(window.location.pathname).toBe("/extract");
    expect(screen.getByRole("heading", { name: "AI智能合同提取工具" })).toBeInTheDocument();
    expect(screen.getByLabelText("上传合同提取文件")).not.toHaveAttribute("multiple");
    expect(screen.queryByText(/数量不超过5份/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始提取" })).toBeDisabled();
  });

  it("enables extraction after a file is selected", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    await user.click(screen.getByRole("button", { name: "合同提取" }));
    await user.upload(screen.getByLabelText("上传合同提取文件"), new File(["contract"], "contract.pdf", { type: "application/pdf" }));

    await user.click(screen.getByRole("button", { name: "开始提取" }));

    expect(screen.getByRole("button", { name: "提取中..." })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("正在提取合同数据...");
  });

  it("adds a created task to comparison records", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "创建任务" }));
    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));

    expect(screen.getByRole("heading", { name: "对比结果 task-2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /task-2/ })).toHaveAttribute("aria-current", "page");
    expect(window.localStorage.getItem("rixin_contract_compare_history")).toContain("task-2");
  });
});
