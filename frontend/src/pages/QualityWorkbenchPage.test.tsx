import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createQualityExpectedDiff,
  evaluateQuality,
  exportQualityCase,
  getQualityCase,
  getQualityTaskReview,
  listQualityCases,
  runQualityRegression,
  updateQualityExpectedDiff,
} from "../lib/api";
import type { QualityCaseDetail, QualityCaseSummary, QualityTaskReviewResponse } from "../types";
import { QualityWorkbenchPage } from "./QualityWorkbenchPage";

vi.mock("../lib/api", () => ({
  listQualityCases: vi.fn(),
  getQualityCase: vi.fn(),
  getQualityTaskReview: vi.fn(),
  updateQualityExpectedDiff: vi.fn(),
  createQualityExpectedDiff: vi.fn(),
  deleteQualityExpectedDiff: vi.fn(),
  exportQualityCase: vi.fn(),
  evaluateQuality: vi.fn(),
  runQualityRegression: vi.fn(),
}));

afterEach(() => {
  vi.resetAllMocks();
});

const caseSummary: QualityCaseSummary = {
  case_id: "case-001",
  schema_version: "1.0",
  dataset_split: "regression",
  case_tags: ["日期", "回归"],
  baseline_required: true,
  source_task_id: "task-001",
  original_filename: "原合同.pdf",
  compare_filename: "新合同.pdf",
  approved_expected_count: 1,
  draft_expected_count: 0,
  rejected_expected_count: 0,
  actual_diff_count: 1,
  has_actual_json: true,
  has_source_pdfs: true,
};

const caseDetail: QualityCaseDetail = {
  summary: caseSummary,
  readme: "gold case readme",
  expected: {
    case_id: "case-001",
    expected_diffs: [
      {
        diff_type: "MODIFY",
        source_type: "clause",
        title_contains: "签订日期",
        original_contains: "2025年1月1日",
        compare_contains: "2025年1月2日",
        review_status: "APPROVED",
      },
    ],
  },
  actual_diffs: [
    {
      diff_id: "diff-001",
      diff_type: "MODIFY",
      source_type: "clause",
      title: "实际日期变更",
      quality_status: "NORMAL",
      review_flags: [],
    },
  ],
};

const caseExpectedDiffs = caseDetail.expected.expected_diffs ?? [];

const secondCaseSummary: QualityCaseSummary = {
  ...caseSummary,
  case_id: "case-002",
  dataset_split: "validation",
  source_task_id: "task-002",
  original_filename: "旧合同.pdf",
  compare_filename: "更新合同.pdf",
};

const secondCaseDetail: QualityCaseDetail = {
  ...caseDetail,
  summary: secondCaseSummary,
  expected: {
    case_id: "case-002",
    expected_diffs: [
      {
        diff_type: "ADD",
        source_type: "amount",
        title_contains: "付款金额",
        original_contains: "100万元",
        compare_contains: "120万元",
        review_status: "APPROVED",
      },
    ],
  },
};

const exportedCaseSummary: QualityCaseSummary = {
  case_id: "task-001",
  schema_version: "1.1",
  dataset_split: "dev",
  case_tags: ["exported", "requires_human_review"],
  baseline_required: false,
  source_task_id: "task-001",
  original_filename: "原合同.pdf",
  compare_filename: "新合同.pdf",
  approved_expected_count: 0,
  draft_expected_count: 10,
  rejected_expected_count: 0,
  actual_diff_count: 10,
  has_actual_json: true,
  has_source_pdfs: false,
};

const exportedCaseDetail: QualityCaseDetail = {
  summary: exportedCaseSummary,
  readme: "exported draft case readme",
  expected: {
    case_id: "task-001",
    source_task_id: "task-001",
    expected_diffs: [
      {
        diff_type: "ADD",
        source_type: "metadata",
        title_contains: "封面字段：合同编号",
        review_status: "DRAFT",
      },
    ],
  },
  actual_diffs: [
    {
      diff_id: "D001",
      diff_type: "ADD",
      source_type: "metadata",
      title: "封面字段：合同编号",
      quality_status: "NEEDS_REVIEW",
      review_flags: ["PAGE_UNRELIABLE"],
    },
  ],
};

const taskReviewResponse: QualityTaskReviewResponse = {
  task_id: "task-001",
  status: "COMPLETED",
  original_filename: "原合同.pdf",
  compare_filename: "新合同.pdf",
  historical_diff_count: 10,
  retained_diff_count: 6,
  suppressed_diff_count: 4,
  ocr_quality_summary: {
    status: "UNRELIABLE",
    requires_review: true,
    risk_page_count: 11,
    affected_diff_count: 9,
  },
  retained_diffs: [
    {
      diff_id: "D001",
      diff_type: "ADD",
      source_type: "metadata",
      title: "封面字段：合同编号",
      quality_status: "NEEDS_REVIEW",
      review_flags: ["PAGE_UNRELIABLE"],
      match_score: null,
      original_snippet: "",
      compare_snippet: "GNXNYN-20140604-000024",
    },
  ],
  suppressed_diffs: [
    {
      diff_id: "D007",
      diff_type: "MODIFY",
      source_type: "clause",
      title: "可行性论证报告:;",
      quality_status: "NEEDS_REVIEW",
      review_flags: ["POSSIBLE_OCR_NOISE"],
      match_score: 100,
      suppression_reason: "clause_ocr_noise",
      quality_decisions: ["possible_ocr_noise", "suppressed_low_value_noise"],
      original_snippet: "/",
      compare_snippet: "",
    },
  ],
  quality_decisions: [
    {
      diff_id: "D007",
      action: "suppressed_low_value_noise",
      detail: {
        reason: "clause_ocr_noise",
      },
    },
  ],
  debug_artifacts: {
    has_diff_quality: true,
    has_diff_decisions: true,
    has_ocr_quality: true,
    has_clause_matches: false,
  },
};

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

describe("QualityWorkbenchPage", () => {
  it("aborts quality reads on unmount without rendering cancellation errors", async () => {
    const casesResponse = createDeferred<{ cases: QualityCaseSummary[] }>();
    let listSignal: AbortSignal | undefined;
    let detailSignal: AbortSignal | undefined;
    let reviewSignal: AbortSignal | undefined;
    vi.mocked(listQualityCases).mockImplementation((signal) => {
      listSignal = signal;
      return casesResponse.promise;
    });
    vi.mocked(getQualityCase).mockImplementation((_caseId, signal) => {
      detailSignal = signal;
      return new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    });
    vi.mocked(getQualityTaskReview).mockImplementation((_taskId, signal) => {
      reviewSignal = signal;
      return new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    });

    const { unmount } = render(<QualityWorkbenchPage />);
    await act(async () => {
      casesResponse.resolve({ cases: [caseSummary] });
      await casesResponse.promise;
    });
    expect(getQualityCase).toHaveBeenCalledWith("case-001", expect.any(AbortSignal));
    await act(async () => {
      fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });
    await vi.waitFor(() => expect(getQualityTaskReview).toHaveBeenCalledWith("task-001", expect.any(AbortSignal)));

    unmount();
    await act(async () => { await Promise.resolve(); });

    expect(listSignal?.aborted).toBe(true);
    expect(detailSignal?.aborted).toBe(true);
    expect(reviewSignal?.aborted).toBe(true);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("loads and renders a task review without changing the selected case", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(getQualityTaskReview).mockResolvedValueOnce(taskReviewResponse);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    expect(getQualityTaskReview).toHaveBeenCalledWith("task-001", expect.any(AbortSignal));
    expect(await screen.findByText("历史 10")).toBeInTheDocument();
    expect(screen.getByText("保留 6")).toBeInTheDocument();
    expect(screen.getByText("抑制 4")).toBeInTheDocument();
    expect(screen.getByText("D007")).toBeInTheDocument();
    expect(screen.getByText("clause_ocr_noise")).toBeInTheDocument();
    expect(screen.getByText("签订日期")).toBeInTheDocument();
  });

  it("defaults draft case id from the loaded task review and disables export before review", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(getQualityTaskReview).mockResolvedValueOnce(taskReviewResponse);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出 Draft Golden Set" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    expect(await screen.findByText("历史 10")).toBeInTheDocument();
    expect(screen.getByLabelText("case_id")).toHaveValue("task-001");
    expect(screen.getByRole("button", { name: "导出 Draft Golden Set" })).toBeEnabled();
  });

  it("exports a loaded task review as a draft golden set and opens the exported case", async () => {
    vi.mocked(listQualityCases)
      .mockResolvedValueOnce({ cases: [caseSummary] })
      .mockResolvedValueOnce({ cases: [caseSummary, exportedCaseSummary] });
    vi.mocked(getQualityCase)
      .mockResolvedValueOnce(caseDetail)
      .mockResolvedValueOnce(exportedCaseDetail);
    vi.mocked(getQualityTaskReview).mockResolvedValueOnce(taskReviewResponse);
    vi.mocked(exportQualityCase).mockResolvedValueOnce({
      case_id: "task-001",
      task_id: "task-001",
      expected_diff_count: 10,
      actual_diff_count: 10,
    });

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "导出 Draft Golden Set" }));
    });

    expect(exportQualityCase).toHaveBeenCalledWith({
      task_id: "task-001",
      case_id: "task-001",
      force: false,
    });
    expect(listQualityCases).toHaveBeenCalledTimes(2);
    expect(getQualityCase).toHaveBeenLastCalledWith("task-001", expect.any(AbortSignal));
    expect(await screen.findByText("已导出 draft golden set：task-001")).toBeInTheDocument();
    expect((await screen.findAllByText("封面字段：合同编号")).length).toBeGreaterThan(1);
  });

  it("shows draft export errors without clearing the loaded task review", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(getQualityTaskReview).mockResolvedValueOnce(taskReviewResponse);
    vi.mocked(exportQualityCase).mockRejectedValueOnce(new Error("Quality case already exists: task-001"));

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "导出 Draft Golden Set" }));
    });

    expect(await screen.findByText("Quality case already exists: task-001")).toBeInTheDocument();
    expect(screen.getByText("历史 10")).toBeInTheDocument();
    expect(screen.getByText("D007")).toBeInTheDocument();
  });

  it("resets draft case id when a different task review is loaded", async () => {
    const secondTaskReview: QualityTaskReviewResponse = {
      ...taskReviewResponse,
      task_id: "task-002",
      historical_diff_count: 3,
      retained_diff_count: 3,
      suppressed_diff_count: 0,
      suppressed_diffs: [],
    };
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(getQualityTaskReview).mockResolvedValueOnce(taskReviewResponse).mockResolvedValueOnce(secondTaskReview);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    expect(screen.getByLabelText("case_id")).toHaveValue("task-001");

    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-002" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    expect(await screen.findByText("历史 3")).toBeInTheDocument();
    expect(screen.getByLabelText("case_id")).toHaveValue("task-002");
  });

  it("ignores stale draft export completion after loading another task review", async () => {
    const exportResult = createDeferred<{
      case_id: string;
      task_id: string;
      expected_diff_count: number;
      actual_diff_count: number;
    }>();
    const secondTaskReview: QualityTaskReviewResponse = {
      ...taskReviewResponse,
      task_id: "task-002",
      historical_diff_count: 3,
      retained_diff_count: 3,
      suppressed_diff_count: 0,
      suppressed_diffs: [],
    };
    vi.mocked(listQualityCases)
      .mockResolvedValueOnce({ cases: [caseSummary] })
      .mockResolvedValueOnce({ cases: [caseSummary, exportedCaseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(getQualityTaskReview).mockResolvedValueOnce(taskReviewResponse).mockResolvedValueOnce(secondTaskReview);
    vi.mocked(exportQualityCase).mockReturnValueOnce(exportResult.promise);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "导出 Draft Golden Set" }));
    });

    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-002" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });
    await act(async () => {
      exportResult.resolve({
        case_id: "task-001",
        task_id: "task-001",
        expected_diff_count: 10,
        actual_diff_count: 10,
      });
      await exportResult.promise;
    });

    expect(screen.queryByText("已导出 draft golden set：task-001")).not.toBeInTheDocument();
    expect(getQualityCase).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("case_id")).toHaveValue("task-002");
  });

  it("renders task review load errors", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(getQualityTaskReview).mockRejectedValueOnce(new Error("任务不存在"));

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "missing-task" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    expect(getQualityTaskReview).toHaveBeenCalledWith("missing-task", expect.any(AbortSignal));
    expect(await screen.findByRole("alert")).toHaveTextContent("任务不存在");
  });

  it("clears stale task review results when a later task review fails", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(getQualityTaskReview)
      .mockResolvedValueOnce(taskReviewResponse)
      .mockRejectedValueOnce(new Error("Quality task not found: missing-task"));

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    expect(await screen.findByText("历史 10")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "missing-task" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("Quality task not found: missing-task");
    expect(screen.queryByText("历史 10")).not.toBeInTheDocument();
    expect(screen.queryByText("D007")).not.toBeInTheDocument();
  });

  it("loads cases, opens the first case, and renders actual and expected diffs", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("质量回归工作台")).toBeInTheDocument();
    expect((await screen.findAllByText("case-001")).length).toBeGreaterThan(0);
    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    expect((await screen.findAllByText("regression")).length).toBeGreaterThan(0);
    expect(getQualityCase).toHaveBeenCalledWith("case-001", expect.any(AbortSignal));
  });

  it("does not let a slower previous case detail overwrite the selected case", async () => {
    const firstDetail = createDeferred<QualityCaseDetail>();
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary, secondCaseSummary] });
    vi.mocked(getQualityCase).mockImplementation((caseId) => {
      if (caseId === "case-001") {
        return firstDetail.promise;
      }
      if (caseId === "case-002") {
        return Promise.resolve(secondCaseDetail);
      }
      return Promise.reject(new Error(`Unexpected case id: ${caseId}`));
    });

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("case-002")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /case-002/ }));

    expect(await screen.findByText("付款金额")).toBeInTheDocument();

    await act(async () => {
      firstDetail.resolve(caseDetail);
      await firstDetail.promise;
    });

    expect(screen.getByText("付款金额")).toBeInTheDocument();
    expect(screen.queryByText("签订日期")).not.toBeInTheDocument();
  });

  it("does not let a slower mutation overwrite the selected case after switching", async () => {
    const updatedFirstDetail: QualityCaseDetail = {
      ...caseDetail,
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          {
            ...caseExpectedDiffs[0],
            review_status: "DRAFT",
          },
        ],
      },
    };
    const updateResult = createDeferred<QualityCaseDetail>();
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary, secondCaseSummary] });
    vi.mocked(getQualityCase).mockImplementation((caseId) => {
      if (caseId === "case-001") {
        return Promise.resolve(caseDetail);
      }
      if (caseId === "case-002") {
        return Promise.resolve(secondCaseDetail);
      }
      return Promise.reject(new Error(`Unexpected case id: ${caseId}`));
    });
    vi.mocked(updateQualityExpectedDiff).mockReturnValueOnce(updateResult.promise);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "待复核" }));
    fireEvent.click(screen.getByRole("button", { name: /case-002/ }));

    expect(await screen.findByText("付款金额")).toBeInTheDocument();

    await act(async () => {
      updateResult.resolve(updatedFirstDetail);
      await updateResult.promise;
    });

    expect(screen.getByText("付款金额")).toBeInTheDocument();
    expect(screen.queryByText("签订日期")).not.toBeInTheDocument();
  });

  it("does not show a stale update error after switching cases", async () => {
    const updateResult = createDeferred<QualityCaseDetail>();
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary, secondCaseSummary] });
    vi.mocked(getQualityCase).mockImplementation((caseId) => {
      if (caseId === "case-001") {
        return Promise.resolve(caseDetail);
      }
      if (caseId === "case-002") {
        return Promise.resolve(secondCaseDetail);
      }
      return Promise.reject(new Error(`Unexpected case id: ${caseId}`));
    });
    vi.mocked(updateQualityExpectedDiff).mockReturnValueOnce(updateResult.promise);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "待复核" }));
    fireEvent.click(screen.getByRole("button", { name: /case-002/ }));

    expect(await screen.findByText("付款金额")).toBeInTheDocument();

    await act(async () => {
      updateResult.reject(new Error("stale update failed"));
      await updateResult.promise.catch(() => undefined);
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("付款金额")).toBeInTheDocument();
    expect(screen.queryByText("签订日期")).not.toBeInTheDocument();
  });

  it("keeps the latest review action result when same-case mutation responses resolve out of order", async () => {
    const draftDetail: QualityCaseDetail = {
      ...caseDetail,
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          {
            ...caseExpectedDiffs[0],
            review_status: "DRAFT",
          },
        ],
      },
    };
    const rejectedDetail: QualityCaseDetail = {
      ...caseDetail,
      summary: {
        ...caseSummary,
        approved_expected_count: 0,
        rejected_expected_count: 1,
      },
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          {
            ...caseExpectedDiffs[0],
            review_status: "REJECTED",
            should_not_match_again: true,
            false_positive_reason: "manual_false_positive",
          },
        ],
      },
    };
    const firstUpdate = createDeferred<QualityCaseDetail>();
    const secondUpdate = createDeferred<QualityCaseDetail>();
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(updateQualityExpectedDiff).mockReturnValueOnce(firstUpdate.promise).mockReturnValueOnce(secondUpdate.promise);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "待复核" }));
    fireEvent.click(screen.getByRole("button", { name: "标为误报" }));

    await act(async () => {
      secondUpdate.resolve(rejectedDetail);
      await secondUpdate.promise;
    });

    expect(screen.getByText("REJECTED")).toBeInTheDocument();

    await act(async () => {
      firstUpdate.resolve(draftDetail);
      await firstUpdate.promise;
    });

    expect(screen.getByText("REJECTED")).toBeInTheDocument();
    expect(screen.queryByText("DRAFT")).not.toBeInTheDocument();
  });

  it("does not show a stale create error after switching cases", async () => {
    const createResult = createDeferred<QualityCaseDetail>();
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary, secondCaseSummary] });
    vi.mocked(getQualityCase).mockImplementation((caseId) => {
      if (caseId === "case-001") {
        return Promise.resolve(caseDetail);
      }
      if (caseId === "case-002") {
        return Promise.resolve(secondCaseDetail);
      }
      return Promise.reject(new Error(`Unexpected case id: ${caseId}`));
    });
    vi.mocked(createQualityExpectedDiff).mockReturnValueOnce(createResult.promise);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "补录漏报" }));
    fireEvent.change(screen.getByLabelText("漏报标题"), { target: { value: "合同金额" } });
    fireEvent.click(screen.getByRole("button", { name: "保存漏报" }));
    fireEvent.click(screen.getByRole("button", { name: /case-002/ }));

    expect(await screen.findByText("付款金额")).toBeInTheDocument();

    await act(async () => {
      createResult.reject(new Error("stale create failed"));
      await createResult.promise.catch(() => undefined);
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("付款金额")).toBeInTheDocument();
    expect(screen.queryByText("签订日期")).not.toBeInTheDocument();
  });

  it("marks an expected diff as negative gold", async () => {
    const rejectedDetail: QualityCaseDetail = {
      ...caseDetail,
      summary: {
        ...caseSummary,
        approved_expected_count: 0,
        rejected_expected_count: 1,
      },
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          {
            ...caseExpectedDiffs[0],
            review_status: "REJECTED",
            should_not_match_again: true,
            false_positive_reason: "manual_false_positive",
          },
        ],
      },
    };
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(updateQualityExpectedDiff).mockResolvedValueOnce(rejectedDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "标为误报" }));
    });

    expect(updateQualityExpectedDiff).toHaveBeenCalledWith("case-001", 0, {
      review_status: "REJECTED",
      should_not_match_again: true,
      false_positive_reason: "manual_false_positive",
    });
  });

  it("creates a negative expected diff from an actual diff", async () => {
    const rejectedDetail: QualityCaseDetail = {
      ...caseDetail,
      summary: {
        ...caseSummary,
        rejected_expected_count: 1,
      },
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          ...caseExpectedDiffs,
          {
            review_status: "REJECTED",
            should_not_match_again: true,
            false_positive_reason: "manual_false_positive",
            source_actual_diff_id: "diff-001",
            diff_type: "MODIFY",
            source_type: "clause",
            title_contains: "实际日期变更",
          },
        ],
      },
    };
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(createQualityExpectedDiff).mockResolvedValueOnce(rejectedDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "标为负向误报" }));
    });

    expect(createQualityExpectedDiff).toHaveBeenCalledWith("case-001", {
      review_status: "REJECTED",
      should_not_match_again: true,
      false_positive_reason: "manual_false_positive",
      source_actual_diff_id: "diff-001",
      diff_type: "MODIFY",
      source_type: "clause",
      title_contains: "实际日期变更",
    });
    expect(await screen.findByText("REJECTED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "已标为负向误报" })).toBeDisabled();
  });

  it("disables negative marking when the actual diff already has a negative expected diff", async () => {
    const negativeDetail: QualityCaseDetail = {
      ...caseDetail,
      summary: {
        ...caseSummary,
        rejected_expected_count: 1,
      },
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          ...caseExpectedDiffs,
          {
            review_status: "REJECTED",
            should_not_match_again: true,
            false_positive_reason: "manual_false_positive",
            source_actual_diff_id: "diff-001",
            diff_type: "MODIFY",
            source_type: "clause",
            title_contains: "实际日期变更",
          },
        ],
      },
    };
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(negativeDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    const negativeButton = screen.getByRole("button", { name: "已标为负向误报" });

    expect(negativeButton).toBeDisabled();
    fireEvent.click(negativeButton);
    expect(createQualityExpectedDiff).not.toHaveBeenCalled();
  });

  it("does not create a negative expected diff twice while save is pending", async () => {
    const rejectedDetail: QualityCaseDetail = {
      ...caseDetail,
      summary: {
        ...caseSummary,
        rejected_expected_count: 1,
      },
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          ...caseExpectedDiffs,
          {
            review_status: "REJECTED",
            should_not_match_again: true,
            false_positive_reason: "manual_false_positive",
            source_actual_diff_id: "diff-001",
            diff_type: "MODIFY",
            source_type: "clause",
            title_contains: "实际日期变更",
          },
        ],
      },
    };
    const createResult = createDeferred<QualityCaseDetail>();
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(createQualityExpectedDiff).mockReturnValueOnce(createResult.promise);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    const negativeButton = screen.getByRole("button", { name: "标为负向误报" });
    fireEvent.click(negativeButton);
    fireEvent.click(negativeButton);

    expect(createQualityExpectedDiff).toHaveBeenCalledTimes(1);

    await act(async () => {
      createResult.resolve(rejectedDetail);
      await createResult.promise;
    });
  });

  it("does not apply a stale negative expected diff result after switching cases", async () => {
    const staleRejectedDetail: QualityCaseDetail = {
      ...caseDetail,
      summary: {
        ...caseSummary,
        rejected_expected_count: 1,
      },
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          ...caseExpectedDiffs,
          {
            review_status: "REJECTED",
            should_not_match_again: true,
            false_positive_reason: "manual_false_positive",
            source_actual_diff_id: "diff-001",
            diff_type: "MODIFY",
            source_type: "clause",
            title_contains: "实际日期变更",
          },
        ],
      },
    };
    const secondDetailWithoutDateActual: QualityCaseDetail = {
      ...secondCaseDetail,
      actual_diffs: [],
    };
    const createResult = createDeferred<QualityCaseDetail>();
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary, secondCaseSummary] });
    vi.mocked(getQualityCase).mockImplementation((caseId) => {
      if (caseId === "case-001") {
        return Promise.resolve(caseDetail);
      }
      if (caseId === "case-002") {
        return Promise.resolve(secondDetailWithoutDateActual);
      }
      return Promise.reject(new Error(`Unexpected case id: ${caseId}`));
    });
    vi.mocked(createQualityExpectedDiff).mockReturnValueOnce(createResult.promise);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "标为负向误报" }));
    fireEvent.click(screen.getByRole("button", { name: /case-002/ }));

    expect(await screen.findByText("付款金额")).toBeInTheDocument();

    await act(async () => {
      createResult.resolve(staleRejectedDetail);
      await createResult.promise;
    });

    expect(screen.getByText("付款金额")).toBeInTheDocument();
    expect(screen.queryByText("实际日期变更")).not.toBeInTheDocument();
  });

  it("shows an error when creating a negative expected diff fails", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(createQualityExpectedDiff).mockRejectedValueOnce(new Error("negative create failed"));

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "标为负向误报" }));
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("negative create failed");
  });

  it("adds a manual missed expected diff", async () => {
    const createdDetail: QualityCaseDetail = {
      ...caseDetail,
      summary: {
        ...caseSummary,
        approved_expected_count: 2,
      },
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          ...caseExpectedDiffs,
          {
            review_status: "APPROVED",
            diff_type: "MODIFY",
            source_type: "metadata",
            title_contains: "合同金额",
            false_negative_reason: "manual_missing_diff",
          },
        ],
      },
    };
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(createQualityExpectedDiff).mockResolvedValueOnce(createdDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "补录漏报" }));
    fireEvent.change(screen.getByLabelText("漏报标题"), { target: { value: "合同金额" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "保存漏报" }));
    });

    expect(createQualityExpectedDiff).toHaveBeenCalledWith("case-001", {
      review_status: "APPROVED",
      diff_type: "MODIFY",
      source_type: "metadata",
      title_contains: "合同金额",
      false_negative_reason: "manual_missing_diff",
    });
  });

  it("updates expected evidence from JSON", async () => {
    const expectedEvidence = [{ side: "compare", page_no: 1, text_contains: "2026年4月21日" }];
    const updatedDetail: QualityCaseDetail = {
      ...caseDetail,
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          {
            ...caseExpectedDiffs[0],
            expected_evidence: expectedEvidence,
          },
        ],
      },
    };
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(updateQualityExpectedDiff).mockResolvedValueOnce(updatedDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑证据" }));
    fireEvent.change(screen.getByLabelText("证据 JSON"), {
      target: { value: JSON.stringify(expectedEvidence) },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "保存证据" }));
    });

    expect(updateQualityExpectedDiff).toHaveBeenCalledWith("case-001", 0, {
      expected_evidence: expectedEvidence,
    });
  });

  it("rejects invalid expected evidence JSON without updating the case", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑证据" }));
    fireEvent.change(screen.getByLabelText("证据 JSON"), {
      target: { value: "{bad json" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存证据" }));

    expect(await screen.findByText("证据 JSON 格式无效。")).toBeInTheDocument();
    expect(updateQualityExpectedDiff).not.toHaveBeenCalled();
  });

  it("rejects non-array expected evidence JSON without updating the case", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑证据" }));
    fireEvent.change(screen.getByLabelText("证据 JSON"), {
      target: { value: JSON.stringify({ side: "compare", page_no: 1 }) },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存证据" }));

    expect(await screen.findByText("证据 JSON 必须是数组。")).toBeInTheDocument();
    expect(updateQualityExpectedDiff).not.toHaveBeenCalled();
  });

  it("runs evaluation and regression from the workbench", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(evaluateQuality).mockResolvedValueOnce({
      run_id: "ui-eval",
      status: "COMPLETED",
      report: {
        aggregate: {
          precision: 1,
          recall: 1,
          known_false_positive_regression_count: 0,
        },
      },
    });
    vi.mocked(runQualityRegression).mockResolvedValueOnce({
      run_id: "ui-regression",
      status: "PASSED",
      report: {},
      comparison: {
        failed_gates: [],
      },
    });

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "运行评估" }));
    });

    expect(evaluateQuality).toHaveBeenCalledWith({ dataset_splits: ["regression"], run_id: "ui-eval" });
    expect(await screen.findByText("Precision 1")).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "运行回归" }));
    });

    expect(runQualityRegression).toHaveBeenCalledWith({
      dataset_splits: ["regression"],
      baseline_name: "current",
      run_id: "ui-regression",
    });
    expect(await screen.findByText("PASSED")).toBeInTheDocument();
  });

  it("does not submit a manual missed diff twice while save is pending", async () => {
    const createdDetail: QualityCaseDetail = {
      ...caseDetail,
      summary: {
        ...caseSummary,
        approved_expected_count: 2,
      },
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          ...caseExpectedDiffs,
          {
            review_status: "APPROVED",
            diff_type: "MODIFY",
            source_type: "metadata",
            title_contains: "合同金额",
            false_negative_reason: "manual_missing_diff",
          },
        ],
      },
    };
    const createResult = createDeferred<QualityCaseDetail>();
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(createQualityExpectedDiff).mockReturnValueOnce(createResult.promise);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "补录漏报" }));
    fireEvent.change(screen.getByLabelText("漏报标题"), { target: { value: "合同金额" } });

    const saveButton = screen.getByRole("button", { name: "保存漏报" });
    fireEvent.click(saveButton);
    fireEvent.click(saveButton);

    expect(createQualityExpectedDiff).toHaveBeenCalledTimes(1);

    await act(async () => {
      createResult.resolve(createdDetail);
      await createResult.promise;
    });
  });
});
