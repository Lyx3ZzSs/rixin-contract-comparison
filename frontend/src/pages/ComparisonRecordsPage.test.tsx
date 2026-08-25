import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getCompareRecords, getTask, retryCompareTask } from "../lib/api";
import { createProgressEventSource } from "../lib/api_sse";
import type { CompareRecordSummary } from "../types";
import { ComparisonRecordsPage } from "./ComparisonRecordsPage";

vi.mock("../lib/api", () => ({
  getCompareRecords: vi.fn(async () => []),
  getTask: vi.fn(),
  retryCompareTask: vi.fn(),
  toApiUrl: (path: string) => `http://api.test${path}`,
}));

const mockEventSource = {
  onmessage: null as ((e: MessageEvent) => void) | null,
  onerror: null as ((error: unknown) => void) | null,
  close: vi.fn(),
};
vi.mock("../lib/api_sse", () => ({
  createProgressEventSource: vi.fn(() => mockEventSource),
}));

const processingRecord: CompareRecordSummary = {
  task_id: "task-processing",
  status: "PROCESSING",
  terminal_reason: "NONE",
  revision: 1,
  report_revision: 0,
  retry_eligible: false,
  stage: "文档解析中",
  progress_percent: 35,
  created_at: "2026-05-12T00:00:00Z",
  updated_at: "2026-05-12T00:00:00Z",
  original_filename: "original.pdf",
  compare_filename: "compare.pdf",
  diff_count: 0,
  report_url: "",
};

const completedRecord: CompareRecordSummary = {
  ...processingRecord,
  task_id: "task-completed",
  status: "COMPLETED",
  updated_at: "2026-05-12T00:01:00Z",
  diff_count: 2,
  report_url: "/api/compare/task-completed/report",
};

const pagePayload = (records: CompareRecordSummary[], page = 1, totalPages = 1) => ({
  records,
  total: records.length,
  page,
  page_size: 10,
  total_pages: totalPages,
});

describe("ComparisonRecordsPage", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.clearAllMocks();
    mockEventSource.onmessage = null;
    mockEventSource.onerror = null;
    mockEventSource.close.mockClear();
    vi.mocked(getTask).mockReset();
    vi.mocked(getCompareRecords).mockReset();
    vi.mocked(retryCompareTask).mockReset();
  });

  it("opens processing records as progress and completed records as results", async () => {
    const onOpenTask = vi.fn();
    vi.mocked(getCompareRecords).mockResolvedValueOnce(pagePayload([processingRecord, completedRecord]));

    render(<ComparisonRecordsPage onOpenTask={onOpenTask} onCreateComparison={vi.fn()} />);

    await screen.findByText("task-processing");
    expect(screen.queryByRole("button", { name: "查看进度" })).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "35");
    expect(screen.getByText("文档解析中")).toBeInTheDocument();
    expect(screen.queryByText("35%")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看结果" })).toBeInTheDocument();
    expect(onOpenTask).not.toHaveBeenCalled();
  });

  it("updates progress via SSE for processing records", async () => {
    vi.mocked(getCompareRecords).mockResolvedValueOnce(pagePayload([processingRecord]));

    render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);

    await screen.findByText("task-processing");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "35");

    vi.useFakeTimers();
    // Simulate SSE progress event (coalesced by the progress throttle before it reaches the UI)
    await act(async () => {
      mockEventSource.onmessage?.({
        data: JSON.stringify({
          task_id: "task-processing",
          stage: "条款匹配中",
          progress_percent: 55,
          status: "PROCESSING",
        }),
      } as MessageEvent);
    });
    await act(async () => {
      vi.advanceTimersByTime(350);
    });

    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "55");
    expect(screen.getByText("条款匹配中")).toBeInTheDocument();
    expect(screen.queryByText("55%")).not.toBeInTheDocument();
  });

  it("refreshes full list when SSE reports completion", async () => {
    vi.mocked(getCompareRecords)
      .mockResolvedValueOnce(pagePayload([processingRecord]))
      .mockResolvedValueOnce(pagePayload([completedRecord]));
    vi.mocked(getTask).mockResolvedValueOnce({
      ...completedRecord,
      original_pdf_url: "/original",
      compare_pdf_url: "/compare",
      original_highlight_pdf_url: "",
      compare_highlight_pdf_url: "",
      report_filename: "report.pdf",
      errors: [],
    });

    render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);

    await screen.findByText("task-processing");
    vi.useFakeTimers();

    // Simulate SSE completion event
    await act(async () => {
      mockEventSource.onmessage?.({
        data: JSON.stringify({
          task_id: "task-processing",
          stage: "已完成",
          progress_percent: 100,
          status: "COMPLETED",
        }),
      } as MessageEvent);
    });

    expect(screen.getByText("收尾完成中")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /收尾完成中/ })).toHaveAttribute("aria-valuenow", "100");
    expect(getCompareRecords).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(900);
      await Promise.resolve();
    });

    expect(getCompareRecords).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "查看结果" })).toBeInTheDocument();
  });

  it("confirms a terminal SSE hint by revision and survives polling network errors", async () => {
    vi.useFakeTimers();
    const completedTask = {
      ...completedRecord,
      created_at: completedRecord.created_at,
      original_pdf_url: "/api/compare/task-processing/original",
      compare_pdf_url: "/api/compare/task-processing/compare",
      original_highlight_pdf_url: "",
      compare_highlight_pdf_url: "",
      report_filename: "report.pdf",
      errors: [],
    };
    vi.mocked(getCompareRecords)
      .mockResolvedValueOnce(pagePayload([processingRecord]))
      .mockResolvedValueOnce(pagePayload([{ ...completedRecord, task_id: "task-processing", revision: 5 }]));
    vi.mocked(getTask)
      .mockRejectedValueOnce(new Error("temporary network error"))
      .mockResolvedValueOnce({ ...completedTask, task_id: "task-processing", revision: 4 })
      .mockResolvedValueOnce({ ...completedTask, task_id: "task-processing", revision: 5 });

    render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      mockEventSource.onmessage?.({ data: JSON.stringify({
        task_id: "task-processing", stage: "已完成", progress_percent: 100, status: "COMPLETED", revision: 5,
      }) } as MessageEvent);
      await Promise.resolve();
    });

    expect(getCompareRecords).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(2400); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(1200); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(900); await Promise.resolve(); await Promise.resolve(); });
    expect(getTask).toHaveBeenCalledTimes(3);
    expect(getCompareRecords).toHaveBeenCalledTimes(2);
  });

  it("falls back to polling when the SSE stream errors", async () => {
    vi.useFakeTimers();
    vi.mocked(getCompareRecords).mockResolvedValueOnce(pagePayload([processingRecord]));
    vi.mocked(getTask).mockResolvedValueOnce({
      ...completedRecord,
      original_pdf_url: "/original",
      compare_pdf_url: "/compare",
      original_highlight_pdf_url: "",
      compare_highlight_pdf_url: "",
      report_filename: "report.pdf",
      errors: [],
    });

    render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); mockEventSource.onerror?.(new Error("down")); });
    await act(async () => { vi.advanceTimersByTime(5000); await Promise.resolve(); await Promise.resolve(); });

    expect(mockEventSource.close).toHaveBeenCalled();
    expect(getTask).toHaveBeenCalled();
  });

  it("labels cancelled records and only offers retry for retryable failures", async () => {
    const cancelled = { ...processingRecord, task_id: "cancelled", status: "FAILED" as const, terminal_reason: "CANCELLED" as const };
    const failed = { ...processingRecord, task_id: "failed", status: "FAILED" as const, terminal_reason: "EXECUTION_FAILED" as const, retry_eligible: undefined };
    const submissionFailed = { ...processingRecord, task_id: "submission-failed", status: "FAILED" as const, terminal_reason: "SUBMISSION_FAILED" as const, retry_eligible: true };
    const legacySubmissionFailed = { ...submissionFailed, task_id: "legacy-submission-failed", retry_eligible: undefined };
    vi.mocked(getCompareRecords).mockResolvedValueOnce(pagePayload([cancelled, failed, submissionFailed, legacySubmissionFailed]));

    render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);

    await screen.findByText("cancelled");
    const cancelledRow = screen.getByText("cancelled").closest("article")!;
    const failedRow = screen.getByText("failed").closest("article")!;
    const submissionFailedRow = screen.getByText("submission-failed").closest("article")!;
    const legacySubmissionFailedRow = screen.getByText("legacy-submission-failed").closest("article")!;
    expect(within(cancelledRow).getByText("已取消")).toBeInTheDocument();
    expect(within(cancelledRow).queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(within(failedRow).getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(within(submissionFailedRow).getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(within(legacySubmissionFailedRow).queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("projects a successful retry to processing, prevents duplicate submits, and keeps syncing if refresh fails", async () => {
    const user = userEvent.setup();
    const failed = { ...processingRecord, task_id: "retry-me", status: "FAILED" as const, terminal_reason: "EXECUTION_FAILED" as const, retry_eligible: true };
    let resolveRetry!: () => void;
    vi.mocked(getCompareRecords)
      .mockResolvedValueOnce(pagePayload([failed]))
      .mockRejectedValueOnce(new Error("refresh failed"));
    vi.mocked(retryCompareTask).mockReturnValueOnce(new Promise((resolve) => { resolveRetry = () => resolve({} as never); }));

    render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);
    const retryButton = await screen.findByRole("button", { name: "重试" });
    await user.click(retryButton);
    await user.click(retryButton);

    expect(retryCompareTask).toHaveBeenCalledTimes(1);
    expect(retryButton).toBeDisabled();

    await act(async () => { resolveRetry(); await Promise.resolve(); await Promise.resolve(); });

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(createProgressEventSource).toHaveBeenCalledWith("retry-me");
    expect(screen.getByText("刷新失败，任务仍在后台同步。" )).toBeInTheDocument();
  });

  it("closes streams and aborts polling when unmounted", async () => {
    vi.useFakeTimers();
    vi.mocked(getCompareRecords).mockResolvedValueOnce(pagePayload([processingRecord]));
    vi.mocked(getTask).mockReturnValueOnce(new Promise(() => undefined));
    const { unmount } = render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByText("task-processing")).toBeInTheDocument();
    await act(async () => { mockEventSource.onerror?.(new Error("down")); });
    await act(async () => { vi.advanceTimersByTime(1200); await Promise.resolve(); });
    const pollingSignal = vi.mocked(getTask).mock.calls[0]?.[1];

    unmount();
    await act(async () => { vi.advanceTimersByTime(20_000); await Promise.resolve(); });

    expect(mockEventSource.close).toHaveBeenCalled();
    expect(pollingSignal?.aborted).toBe(true);
    expect(getTask).toHaveBeenCalledTimes(1);
  });

  it("aborts record reads on unmount without rendering a cancellation error", async () => {
    let requestSignal: AbortSignal | undefined;
    vi.mocked(getCompareRecords).mockImplementation((_query, signal) => {
      requestSignal = signal;
      return new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    });

    const { unmount } = render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);
    await vi.waitFor(() => expect(getCompareRecords).toHaveBeenCalledTimes(1));
    unmount();
    await act(async () => { await Promise.resolve(); });

    expect(requestSignal?.aborted).toBe(true);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("applies updated date filters from the toolbar", async () => {
    const user = userEvent.setup();
    vi.mocked(getCompareRecords)
      .mockResolvedValueOnce(pagePayload([completedRecord]))
      .mockResolvedValueOnce(pagePayload([]));

    render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);

    await screen.findByText("task-completed");
    await user.type(screen.getByLabelText("创建开始日期"), "2026-05-21");
    await user.type(screen.getByLabelText("创建结束日期"), "2026-05-22");
    await user.click(screen.getByRole("button", { name: "查询" }));

    expect(getCompareRecords).toHaveBeenLastCalledWith({
      page: 1,
      pageSize: 10,
      startDate: "2026-05-21",
      endDate: "2026-05-22",
    }, expect.any(AbortSignal));
    expect(await screen.findByText("暂无对比记录")).toBeInTheDocument();
    expect(screen.queryByLabelText("对比记录分页")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "上一页" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "下一页" })).not.toBeInTheDocument();
  });

  it("loads the next page of records", async () => {
    const user = userEvent.setup();
    const nextRecord = { ...completedRecord, task_id: "task-next", updated_at: "2026-05-12T00:02:00Z" };
    vi.mocked(getCompareRecords)
      .mockResolvedValueOnce({ ...pagePayload([completedRecord], 1, 2), total: 2 })
      .mockResolvedValueOnce({ ...pagePayload([nextRecord], 2, 2), total: 2 });

    render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);

    await screen.findByText("task-completed");
    expect(screen.getByLabelText("对比记录分页")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下一页" }));

    expect(getCompareRecords).toHaveBeenLastCalledWith({
      page: 2,
      pageSize: 10,
      startDate: "",
      endDate: "",
    }, expect.any(AbortSignal));
    expect(await screen.findByText("task-next")).toBeInTheDocument();
  });
});
