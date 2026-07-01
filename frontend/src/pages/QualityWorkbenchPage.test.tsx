import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createQualityExpectedDiff,
  evaluateQuality,
  getQualityCase,
  listQualityCases,
  runQualityRegression,
  updateQualityExpectedDiff,
} from "../lib/api";
import type { QualityCaseDetail, QualityCaseSummary } from "../types";
import { QualityWorkbenchPage } from "./QualityWorkbenchPage";

vi.mock("../lib/api", () => ({
  listQualityCases: vi.fn(),
  getQualityCase: vi.fn(),
  updateQualityExpectedDiff: vi.fn(),
  createQualityExpectedDiff: vi.fn(),
  deleteQualityExpectedDiff: vi.fn(),
  exportQualityCase: vi.fn(),
  evaluateQuality: vi.fn(),
  runQualityRegression: vi.fn(),
}));

afterEach(() => {
  vi.clearAllMocks();
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
  it("loads cases, opens the first case, and renders actual and expected diffs", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("质量回归工作台")).toBeInTheDocument();
    expect((await screen.findAllByText("case-001")).length).toBeGreaterThan(0);
    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    expect((await screen.findAllByText("regression")).length).toBeGreaterThan(0);
    expect(getQualityCase).toHaveBeenCalledWith("case-001");
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
