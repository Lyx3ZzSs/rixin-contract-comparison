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

vi.mock("./pages/ComparisonRecordsPage", () => ({
  ComparisonRecordsPage: ({
    onOpenTask,
    onCreateComparison,
  }: {
    onOpenTask: (taskId: string) => void;
    onCreateComparison: () => void;
  }) => (
    <section>
      <h1>对比记录</h1>
      <button type="button" onClick={() => onOpenTask("task-1")}>
        查看结果
      </button>
      <button type="button" onClick={onCreateComparison}>
        新建合同对比
      </button>
    </section>
  ),
}));

vi.mock("./pages/ExtractionRecordsPage", () => ({
  ExtractionRecordsPage: ({ onCreateExtraction }: { onCreateExtraction: () => void }) => (
    <section>
      <h1>提取记录</h1>
      <button type="button" onClick={onCreateExtraction}>
        新建合同提取
      </button>
    </section>
  ),
}));

vi.mock("./pages/ExtractionFieldsPage", () => ({
  ExtractionFieldsPage: () => (
    <section>
      <h1>提取字段管理</h1>
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

  it("opens comparison records as a standalone page from the expanded contract menu", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    await user.click(screen.getByRole("button", { name: "对比记录" }));

    expect(screen.getByRole("button", { name: "合同智能对比" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "合同对比" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "对比记录" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByLabelText("合同智能对比菜单")).toHaveTextContent("对比记录");
    expect(window.location.pathname).toBe("/compare/records");
    expect(screen.getByRole("heading", { name: "对比记录" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /task-1/ })).not.toBeInTheDocument();
  });

  it("opens a task from the standalone comparison records page", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");
    window.history.replaceState({}, "", "/compare/records");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "查看结果" }));

    expect(window.location.pathname).toBe("/tasks/task-1");
    expect(screen.getByRole("heading", { name: "对比结果 task-1" })).toBeInTheDocument();
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
    expect(screen.queryByRole("button", { name: "开始提取" })).not.toBeInTheDocument();
  });

  it("opens extraction records from the extraction menu", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    await user.click(screen.getByRole("button", { name: "提取记录" }));

    expect(window.location.pathname).toBe("/extract/records");
    expect(screen.getByRole("button", { name: "提取记录" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { name: "提取记录" })).toBeInTheDocument();
  });

  it("opens extraction field management from the extraction menu", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    await user.click(screen.getByRole("button", { name: "提取字段管理" }));

    expect(window.location.pathname).toBe("/extract/fields");
    expect(screen.getByRole("button", { name: "提取字段管理" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { name: "提取字段管理" })).toBeInTheDocument();
  });

  it("starts a new extraction from the extraction records page", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");
    window.history.replaceState({}, "", "/extract/records");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "新建合同提取" }));

    expect(window.location.pathname).toBe("/extract");
    expect(screen.getByRole("heading", { name: "AI智能合同提取工具" })).toBeInTheDocument();
  });

  it("opens field setup after selecting a file", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    await user.click(screen.getByRole("button", { name: "合同提取" }));
    await user.upload(screen.getByLabelText("上传合同提取文件"), new File(["contract"], "contract.pdf", { type: "application/pdf" }));

    expect(screen.queryByText("即将进行合同提取，请稍后")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "字段列表" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始提取" })).toBeInTheDocument();
    expect(screen.getByLabelText("合同文件列表")).toHaveTextContent("contract.pdf");
    expect(screen.getByLabelText("提取字段列表")).toHaveTextContent("甲方名称");
    expect(screen.queryByText("提取ID:")).not.toBeInTheDocument();
  });

  it("opens a newly created task without writing sidebar history", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("rixin_contract_auth_user", "admin");

    render(<App />);

    await user.click(screen.getByRole("button", { name: "创建任务" }));

    expect(screen.getByRole("heading", { name: "对比结果 task-2" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/tasks/task-2");
    expect(window.localStorage.getItem("rixin_contract_compare_history")).toBeNull();
  });
});
