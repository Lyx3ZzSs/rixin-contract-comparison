import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getCompareRecords } from "../lib/api";
import type { CompareRecordSummary } from "../types";
import { ComparisonRecordsPage } from "./ComparisonRecordsPage";

vi.mock("../lib/api", () => ({
  getCompareRecords: vi.fn(async () => []),
  toApiUrl: (path: string) => `http://api.test${path}`,
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
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("opens processing records as progress and completed records as results", async () => {
    const onOpenTask = vi.fn();
    vi.mocked(getCompareRecords).mockResolvedValueOnce([processingRecord, completedRecord]);

    render(<ComparisonRecordsPage onOpenTask={onOpenTask} onCreateComparison={vi.fn()} />);

    await screen.findByText("task-processing");
    expect(screen.queryByRole("button", { name: "查看进度" })).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "35");
    expect(screen.getByText("文档解析中")).toBeInTheDocument();
    expect(screen.getByText("35%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看结果" })).toBeInTheDocument();
    expect(onOpenTask).not.toHaveBeenCalled();
  });

  it("polls records while any comparison is processing", async () => {
    let scheduledCallback: (() => void) | undefined;
    vi.spyOn(window, "setTimeout").mockImplementation((handler) => {
      scheduledCallback = typeof handler === "function" ? () => handler() : undefined;
      return 1 as unknown as ReturnType<typeof window.setTimeout>;
    });
    vi.mocked(getCompareRecords)
      .mockResolvedValueOnce([processingRecord])
      .mockResolvedValueOnce([completedRecord]);

    render(<ComparisonRecordsPage onOpenTask={vi.fn()} onCreateComparison={vi.fn()} />);

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText("task-processing")).toBeInTheDocument();
    expect(getCompareRecords).toHaveBeenCalledTimes(1);
    expect(scheduledCallback).toBeDefined();

    await act(async () => {
      scheduledCallback?.();
      await Promise.resolve();
    });

    expect(getCompareRecords).toHaveBeenCalledTimes(2);
    expect(screen.getByText("task-completed")).toBeInTheDocument();
  });
});
