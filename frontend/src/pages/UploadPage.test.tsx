import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { compareContracts } from "../lib/api";
import { UploadPage } from "./UploadPage";

vi.mock("../lib/api", () => ({
  compareContracts: vi.fn(async () => ({ task_id: "task-1", diff_count: 1 })),
}));

describe("UploadPage", () => {
  it("renders the contract comparison workspace and keeps upload controls", () => {
    render(<UploadPage onTaskCreated={vi.fn()} />);

    expect(screen.getByText("智能合同对比")).toBeInTheDocument();
    expect(screen.getByLabelText("原版文件")).toBeInTheDocument();
    expect(screen.getByLabelText("新版文件")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始对比" })).toBeDisabled();
    expect(screen.queryByText("等待上传两份 PDF 合同")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "排除对比项" })).not.toBeInTheDocument();
  });

  it("submits basic comparison without blocking on AI analysis", async () => {
    const user = userEvent.setup();
    const onTaskCreated = vi.fn();
    render(<UploadPage onTaskCreated={onTaskCreated} />);

    await user.upload(screen.getByLabelText("原版文件"), new File(["original"], "original.pdf", { type: "application/pdf" }));
    await user.upload(screen.getByLabelText("新版文件"), new File(["compare"], "compare.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "开始对比" }));

    await waitFor(() => expect(compareContracts).toHaveBeenCalled());
    expect(compareContracts).toHaveBeenCalledWith(expect.any(File), expect.any(File));
    expect(onTaskCreated).toHaveBeenCalledWith("task-1");
  });

  it("shows uploaded file actions and clears a selected file", async () => {
    const user = userEvent.setup();
    render(<UploadPage onTaskCreated={vi.fn()} />);

    await user.upload(screen.getByLabelText("原版文件"), new File(["original"], "original.pdf", { type: "application/pdf" }));
    await user.upload(screen.getByLabelText("新版文件"), new File(["compare"], "compare.pdf", { type: "application/pdf" }));

    expect(screen.getByText("原版文件上传成功")).toBeInTheDocument();
    expect(screen.getByText("新版文件上传成功")).toBeInTheDocument();
    expect(screen.getByText("original.pdf")).toBeInTheDocument();
    expect(screen.getByText("compare.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始对比" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "删除原版文件" }));

    expect(screen.queryByText("原版文件上传成功")).not.toBeInTheDocument();
    expect(screen.queryByText("original.pdf")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始对比" })).toBeDisabled();
  });

  it("reuploads and replaces a selected file", async () => {
    const user = userEvent.setup();
    render(<UploadPage onTaskCreated={vi.fn()} />);

    await user.upload(screen.getByLabelText("新版文件"), new File(["compare"], "compare.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "重新上传新版文件" }));
    await user.upload(screen.getByLabelText("新版文件"), new File(["replace"], "replace.pdf", { type: "application/pdf" }));

    expect(screen.queryByText("compare.pdf")).not.toBeInTheDocument();
    expect(screen.getByText("replace.pdf")).toBeInTheDocument();
  });
});
