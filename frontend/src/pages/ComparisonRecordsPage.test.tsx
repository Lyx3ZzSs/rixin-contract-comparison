import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getCompareRecords } from "../lib/api";
import type { CompareRecordSummary } from "../types";
import { ComparisonRecordsPage } from "./ComparisonRecordsPage";

vi.mock("../lib/api", () => ({
  getCompareRecords: vi.fn(async () => []),
  toApiUrl: (path: string) => `http://api.test${path}`,
}));

const mockEventSource = {
  onmessage: null as ((e: MessageEvent) => void) | null,
  onerror: null as (() => void) | null,
  close: vi.fn(),
};
vi.mock("../lib/api_sse", () => ({
  createProgressEventSource: vi.fn(() => mockEventSource),
}));

const processingRecord: CompareRecordSummary = {
  task_id: "task-processing",
  status: "PROCESSING",
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

describe("ComparisonRecordsPage", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.clearAllMocks();
    mockEventSource.onmessage = null;
    mockEventSource.onerror = null;
    mockEventSource.close.mockClear();
  });

  it("opens processing records as progress and completed records as results", async () => {
    const onOpenTask = vi.fn();
    vi.mocked(getCompareRecords).mockResolvedValueOnce([processingRecord, completedRecord]);

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
    vi.mocked(getCompareRecords).mockResolvedValueOnce([processingRecord]);

    render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);

    await screen.findByText("task-processing");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "35");

    // Simulate SSE progress event
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

    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "55");
    expect(screen.getByText("条款匹配中")).toBeInTheDocument();
    expect(screen.queryByText("55%")).not.toBeInTheDocument();
  });

  it("refreshes full list when SSE reports completion", async () => {
    vi.mocked(getCompareRecords)
      .mockResolvedValueOnce([processingRecord])
      .mockResolvedValueOnce([completedRecord]);

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
});
