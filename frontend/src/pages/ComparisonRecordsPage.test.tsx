import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  created_at: "2026-05-12T00:00:00Z",
  updated_at: "2026-05-12T00:00:00Z",
  original_filename: "original.pdf",
  compare_filename: "compare.pdf",
  diff_count: 0,
  high_risk_count: 0,
  medium_risk_count: 0,
  low_risk_count: 0,
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
    const user = userEvent.setup();
    const onOpenTask = vi.fn();
    vi.mocked(getCompareRecords).mockResolvedValueOnce([processingRecord, completedRecord]);

    render(<ComparisonRecordsPage onOpenTask={onOpenTask} onCreateComparison={vi.fn()} />);

    await screen.findByText("task-processing");
    expect(screen.getByRole("button", { name: "查看进度" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看结果" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "查看进度" }));

    expect(onOpenTask).toHaveBeenCalledWith("task-processing");
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
