import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { compareContracts } from "../lib/api";
import { UploadPage } from "./UploadPage";

vi.mock("../lib/api", () => ({
  compareContracts: vi.fn(async () => ({
    task_id: "task-1",
    status: "PROCESSING",
    stage: "文档解析中",
    progress_percent: 8,
    diff_count: 0,
  })),
}));

const defaultProps = {
  onTaskCreated: vi.fn(),
  onOpenRecords: vi.fn(),
};

describe("UploadPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the contract comparison workspace and keeps upload controls", () => {
    render(<UploadPage {...defaultProps} />);

    expect(screen.getByText("智能合同对比")).toBeInTheDocument();
    expect(screen.getByLabelText("原版文件")).toBeInTheDocument();
    expect(screen.getByLabelText("新版文件")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始对比" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "使用示例" })).not.toBeInTheDocument();
    expect(screen.queryByText("等待上传两份 PDF 合同")).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "排除对比项" })).toBeInTheDocument();
    expect(screen.getByLabelText("排除印章区域")).not.toBeChecked();
    expect(screen.getByLabelText("排除印章区域")).toHaveAccessibleDescription(
      "忽略印章及其遮挡差异，不影响签字和签署信息比对",
    );
    expect(screen.getByLabelText("排除页眉页脚差异项")).not.toBeChecked();
    expect(screen.getByLabelText("排除页眉页脚差异项")).toHaveAccessibleDescription("不生成页眉、页脚、页码等差异");
  });

  it("submits basic comparison without blocking on AI analysis", async () => {
    const user = userEvent.setup();
    const onTaskCreated = vi.fn();
    render(<UploadPage onTaskCreated={onTaskCreated} onOpenRecords={vi.fn()} />);

    await user.upload(screen.getByLabelText("原版文件"), new File(["original"], "original.pdf", { type: "application/pdf" }));
    await user.upload(screen.getByLabelText("新版文件"), new File(["compare"], "compare.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "开始对比" }));

    await waitFor(() => expect(compareContracts).toHaveBeenCalled());
    expect(compareContracts).toHaveBeenCalledWith(expect.any(File), expect.any(File), {
      ignoreStamps: false,
      ignoreHeadersFooters: false,
      signingRegionMode: "full",
    });
    expect(onTaskCreated).toHaveBeenCalledWith("task-1");
    const taskNotice = screen.getByRole("status", { name: "后台对比任务通知" });
    expect(taskNotice).toHaveTextContent("后台比对已开始");
    expect(taskNotice).toHaveTextContent("任务编号：task-1");
    expect(taskNotice).toHaveTextContent("文档解析中");
    expect(taskNotice).not.toHaveTextContent("8%");
    expect(screen.getByRole("progressbar", { name: /文档解析中/ })).toHaveAttribute("aria-valuenow", "8");
    expect(screen.queryByText("original.pdf")).not.toBeInTheDocument();
    expect(screen.queryByText("compare.pdf")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始对比" })).toBeDisabled();
  });

  it("submits the stamp exclusion option when selected", async () => {
    const user = userEvent.setup();
    render(<UploadPage onTaskCreated={vi.fn()} onOpenRecords={vi.fn()} />);

    await user.upload(screen.getByLabelText("原版文件"), new File(["original"], "original.pdf", { type: "application/pdf" }));
    await user.upload(screen.getByLabelText("新版文件"), new File(["compare"], "compare.pdf", { type: "application/pdf" }));
    await user.click(screen.getByLabelText("排除印章区域"));
    await user.click(screen.getByRole("button", { name: "开始对比" }));

    await waitFor(() => expect(compareContracts).toHaveBeenCalled());
    expect(compareContracts).toHaveBeenCalledWith(expect.any(File), expect.any(File), {
      ignoreStamps: true,
      ignoreHeadersFooters: false,
      signingRegionMode: "off",
    });
  });

  it("submits the header footer exclusion option when selected", async () => {
    const user = userEvent.setup();
    render(<UploadPage onTaskCreated={vi.fn()} onOpenRecords={vi.fn()} />);

    await user.upload(screen.getByLabelText("原版文件"), new File(["original"], "original.pdf", { type: "application/pdf" }));
    await user.upload(screen.getByLabelText("新版文件"), new File(["compare"], "compare.pdf", { type: "application/pdf" }));
    await user.click(screen.getByLabelText("排除页眉页脚差异项"));
    await user.click(screen.getByRole("button", { name: "开始对比" }));

    await waitFor(() => expect(compareContracts).toHaveBeenCalled());
    expect(compareContracts).toHaveBeenCalledWith(expect.any(File), expect.any(File), {
      ignoreStamps: false,
      ignoreHeadersFooters: true,
      signingRegionMode: "full",
    });
  });

  it("opens comparison records from the background task notice", async () => {
    const user = userEvent.setup();
    const onOpenRecords = vi.fn();
    render(<UploadPage onTaskCreated={vi.fn()} onOpenRecords={onOpenRecords} />);

    await user.upload(screen.getByLabelText("原版文件"), new File(["original"], "original.pdf", { type: "application/pdf" }));
    await user.upload(screen.getByLabelText("新版文件"), new File(["compare"], "compare.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "开始对比" }));

    await user.click(await screen.findByRole("button", { name: "查看对比记录" }));

    expect(onOpenRecords).toHaveBeenCalled();
  });

  it("hides the background task notice after a few seconds", async () => {
    const user = userEvent.setup();
    let resolveComparison!: (value: Awaited<ReturnType<typeof compareContracts>>) => void;
    vi.mocked(compareContracts).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveComparison = resolve;
        }),
    );
    render(<UploadPage onTaskCreated={vi.fn()} onOpenRecords={vi.fn()} taskToastDurationMs={10} />);

    await user.upload(screen.getByLabelText("原版文件"), new File(["original"], "original.pdf", { type: "application/pdf" }));
    await user.upload(screen.getByLabelText("新版文件"), new File(["compare"], "compare.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "开始对比" }));

    try {
      vi.useFakeTimers();
      await act(async () => {
        resolveComparison({
          task_id: "task-1",
          status: "PROCESSING",
          terminal_reason: "NONE",
          revision: 1,
          report_revision: 0,
          stage: "文档解析中",
          progress_percent: 8,
          diff_count: 0,
          report_url: "",
          report_filename: "",
          original_pdf_url: "",
          compare_pdf_url: "",
          original_highlight_pdf_url: "",
          compare_highlight_pdf_url: "",
          errors: [],
        });
      });

      expect(screen.getByRole("status", { name: "后台对比任务通知" })).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(10);
      });

      expect(screen.queryByRole("status", { name: "后台对比任务通知" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows uploaded file actions and clears a selected file", async () => {
    const user = userEvent.setup();
    render(<UploadPage {...defaultProps} />);

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
    render(<UploadPage {...defaultProps} />);

    await user.upload(screen.getByLabelText("新版文件"), new File(["compare"], "compare.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "重新上传新版文件" }));
    await user.upload(screen.getByLabelText("新版文件"), new File(["replace"], "replace.pdf", { type: "application/pdf" }));

    expect(screen.queryByText("compare.pdf")).not.toBeInTheDocument();
    expect(screen.getByText("replace.pdf")).toBeInTheDocument();
  });
});
