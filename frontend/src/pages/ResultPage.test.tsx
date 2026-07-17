import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getDiffs, getTask, updateAuditItemReview } from "../lib/api";
import { createProgressEventSource } from "../lib/api_sse";
import type { CompareTask, DiffItem } from "../types";
import { ResultPage } from "./ResultPage";

const { downloadAuthenticatedFile } = vi.hoisted(() => ({
  downloadAuthenticatedFile: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("../lib/authFetch", () => ({ downloadAuthenticatedFile }));

vi.mock("../components/PdfDocumentViewer", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  return {
    PdfDocumentViewer: React.forwardRef(
      (
        props: {
          side: "original" | "compare";
          src: string;
          title: string;
          zoom: number;
          hidden?: boolean;
          onActivateDiff: (diffId: string) => void;
        },
        ref,
      ) => {
        React.useImperativeHandle(ref, () => ({
          scrollToDiff: vi.fn(),
          syncScrollFrom: vi.fn(),
        }));
        return (
          <article aria-label={`${props.title}PDF 在线预览`} data-side={props.side} data-src={props.src}>
            <span>{Math.round(props.zoom * 100)}%</span>
            {props.hidden ? <span>原版已隐藏</span> : <button onClick={() => props.onActivateDiff("diff-1")}>定位差异</button>}
          </article>
        );
      },
    ),
  };
});

vi.mock("../lib/api", () => ({
  getTask: vi.fn(async () => mockTask),
  getDiffs: vi.fn(async () => mockDiffs),
  retryCompareTask: vi.fn(),
  getCompareQuality: vi.fn(async () => mockQuality),
  updateAuditItemReview: vi.fn(async (
    _taskId: string,
    auditItemId: string,
    payload: { review_status: string; review_comment?: string },
  ) => ({
    task_id: "task-1",
    audit_item_id: auditItemId,
    audit_item_review: {
      audit_item_id: auditItemId,
      review_status: payload.review_status,
      review_comment: payload.review_comment ?? "",
      reviewed_by: "keycloak-user",
      reviewed_at: "2026-05-12T00:00:20Z",
    },
    review_stats: {
      reviewed_count: 1,
      confirmed_count: payload.review_status === "CONFIRMED" ? 1 : 0,
      false_positive_count: payload.review_status === "FALSE_POSITIVE" ? 1 : 0,
      manual_review_count: payload.review_status === "NEEDS_REVIEW" ? 1 : 0,
      ignored_count: payload.review_status === "IGNORED" ? 1 : 0,
    },
  })),
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

const mockTask: CompareTask = {
  task_id: "task-1",
  status: "COMPLETED",
  terminal_reason: "NONE",
  revision: 2,
  report_revision: 1,
  retry_eligible: false,
  stage: "已完成",
  progress_percent: 100,
  created_at: "2026-05-12T00:00:00Z",
  updated_at: "2026-05-12T00:00:10Z",
  original_filename: "original.pdf",
  compare_filename: "compare.pdf",
  diff_count: 3,
  reviewed_count: 0,
  confirmed_count: 0,
  false_positive_count: 0,
  manual_review_count: 0,
  ignored_count: 0,
  audit_item_reviews: {},
  ocr_remediation_summary: {
    status: "ACTIONS_PLANNED",
    requires_manual_review: false,
    attempted_action_count: 1,
    successful_action_count: 0,
    unresolved_action_count: 1,
    risk_reduced_page_count: 0,
    risk_reduced_diff_count: 0,
    manual_review_required_count: 0,
    actions: [
      {
        action_id: "original:1:diff-1:RELOCATE_EVIDENCE",
        action_type: "RELOCATE_EVIDENCE",
        reason: "EVIDENCE_UNRELIABLE",
        status: "PLANNED",
        side: "original",
        page_no: 1,
        diff_id: "diff-1",
        before_quality: {},
        after_quality: {},
        changed_evidence: false,
        changed_diff_text: false,
        review_flags_added: ["OCR_REMEDIATION_PLANNED"],
        notes: ["Planning-only action for EVIDENCE_UNRELIABLE."],
      },
    ],
  },
  report_url: "/api/compare/task-1/report",
  report_filename: "销售合同差异分析报告.pdf",
  original_pdf_url: "/api/compare/task-1/original",
  compare_pdf_url: "/api/compare/task-1/compare",
  original_highlight_pdf_url: "",
  compare_highlight_pdf_url: "",
  errors: [],
};

const mockDiffs: DiffItem[] = [
  {
    diff_id: "diff-1",
    diff_type: "MODIFY",
    clause_no: "1",
    title: "付款",
    original_text: "Buyer shall pay within 30 days.",
    compare_text: "Buyer shall pay within 45 days.",
    original_snippet: "30 days",
    compare_snippet: "45 days",
    readable_change: "付款期限由 30 天调整为 45 天。",
    source_type: "clause",
    match_score: 62,
    match_method: "same_clause_no_low_similarity",
    review_flags: [
      "SAME_CLAUSE_NO_LOW_SIMILARITY",
      "OCR_LOW_CONFIDENCE",
      "PAGE_UNRELIABLE",
      "TABLE_STRUCTURE_UNRELIABLE",
      "LAYOUT_MISMATCH_RISK",
      "READING_ORDER_RISK",
      "SEAL_OR_SIGNATURE_RISK",
      "EVIDENCE_UNRELIABLE",
    ],
    quality_status: "NEEDS_REVIEW",
    review_status: "UNREVIEWED",
    review_comment: "",
    original_evidence: [
      {
        page_no: 1,
        bbox: { x0: 72, y0: 120, x1: 240, y1: 146 },
        method: "block",
        text: "30 days",
        highlight_type: "MODIFY",
        confidence: 0.46,
        evidence_quality: "LOW",
      },
    ],
    compare_evidence: [
      {
        page_no: 1,
        bbox: { x0: 72, y0: 120, x1: 240, y1: 146 },
        method: "block",
        text: "45 days",
        highlight_type: "MODIFY",
        confidence: 0.98,
        evidence_quality: "HIGH",
      },
      {
        page_no: 1,
        bbox: { x0: 250, y0: 120, x1: 340, y1: 146 },
        method: "block",
        text: "新增付款说明",
        highlight_type: "ADD",
        confidence: 0.98,
        evidence_quality: "HIGH",
      },
    ],
  },
  {
    diff_id: "diff-2",
    diff_type: "ADD",
    clause_no: "2",
    title: "发票",
    original_text: "",
    compare_text: "Seller shall provide invoice.",
    original_snippet: "",
    compare_snippet: "新增发票条款",
    readable_change: "新增发票条款。",
    source_type: "metadata",
    review_flags: ["SEAL_REVIEW"],
    review_status: "UNREVIEWED",
    original_evidence: [],
    compare_evidence: [
      {
        page_no: 1,
        bbox: { x0: 72, y0: 60, x1: 240, y1: 86 },
        method: "block",
        text: "",
        highlight_type: "ADD",
      },
    ],
  },
  {
    diff_id: "diff-3",
    diff_type: "DELETE",
    clause_no: "3",
    title: "旧质保",
    original_text: "Warranty lasts 12 months.",
    compare_text: "",
    original_snippet: "12 months",
    compare_snippet: "",
    readable_change: "删除旧质保约定。",
    source_type: "clause",
    review_status: "UNREVIEWED",
    original_evidence: [
      {
        page_no: 1,
        bbox: { x0: 72, y0: 240, x1: 240, y1: 266 },
        method: "block",
        text: "12 months",
        highlight_type: "DELETE",
      },
    ],
    compare_evidence: [],
  },
  {
    diff_id: "diff-4",
    diff_type: "DELETE",
    clause_no: "4",
    title: "无定位表格项",
    original_text: "3 | 短期模型 | 光伏场短期功率预报 模型开发。 | 国能日新 | 套 | 1",
    compare_text: "",
    original_snippet: "3 | 短期模型 | 光伏场短期功率预报 模型开发。 | 国能日新 | 套 | 1",
    compare_snippet: "",
    readable_change: "表格行38: 删除 '3', 删除 '短期模型'",
    source_type: "table",
    review_flags: ["LOW_CONFIDENCE_ORIGINAL_TABLE_EVIDENCE"],
    review_status: "UNREVIEWED",
    original_evidence: [],
    compare_evidence: [],
  },
];

const preambleReplacementDiff: DiffItem = {
  diff_id: "diff-preamble-title",
  diff_type: "MODIFY",
  clause_no: "",
  title: "前置标题（第2页）",
  original_text: "国能长源随州发电有限公司随县分公司\n2026年新能源场站功率预测系统授权服务合同",
  compare_text: "长源电力随州公司2026年新能源场站\n功率预测系统授权服务单一来源项目合同",
  original_snippet: "国能长源随州发电有限公司随县分公司\n2026年新能源场站功率预测系统授权服务合同",
  compare_snippet: "长源电力随州公司2026年新能源场站\n功率预测系统授权服务单一来源项目合同",
  readable_change: "前置标题（第2页）完整变更",
  source_type: "metadata",
  review_status: "UNREVIEWED",
  original_evidence: [
    {
      page_no: 2,
      bbox: { x0: 130, y0: 80, x1: 470, y1: 102 },
      method: "cover_metadata",
      text: "国能长源随州发电有限公司随县分公司",
      highlight_type: "MODIFY",
    },
    {
      page_no: 2,
      bbox: { x0: 115, y0: 112, x1: 300, y1: 136 },
      method: "cover_metadata",
      text: "2026年新能源场站功率预测系统授权服务合同",
      highlight_type: "MODIFY",
    },
  ],
  compare_evidence: [
    {
      page_no: 2,
      bbox: { x0: 138, y0: 82, x1: 375, y1: 103 },
      method: "cover_metadata",
      text: "长源电力随州公司2026年新能源场站",
      highlight_type: "MODIFY",
    },
    {
      page_no: 2,
      bbox: { x0: 272, y0: 115, x1: 358, y1: 136 },
      method: "cover_metadata",
      text: "功率预测系统授权服务单一来源项目合同",
      highlight_type: "MODIFY",
    },
  ],
};

const signingRegionDiff: DiffItem = {
  diff_id: "diff-signing",
  diff_type: "ADD",
  clause_no: "",
  title: "签章区",
  original_text: "",
  compare_text: "新增授权代表签字",
  original_snippet: "",
  compare_snippet: "新增授权代表签字",
  readable_change: "新增签章区内容。",
  source_type: "signing_region",
  review_flags: ["SIGNING_REGION_VISUAL_CHANGE"],
  review_status: "UNREVIEWED",
  original_evidence: [],
  compare_evidence: [
    {
      page_no: 1,
      bbox: { x0: 360, y0: 680, x1: 500, y1: 740 },
      method: "signing_region_visual",
      text: "新增授权代表签字",
      highlight_type: "ADD",
    },
  ],
};

const signingPartyModifyDiff: DiffItem = {
  diff_id: "diff-signing-party",
  diff_type: "MODIFY",
  clause_no: "",
  title: "签署区（第53页）",
  original_text: "甲方：国能长源随州发电有限公司\n乙方：国能日新科技股份有限公司\n随县分公司",
  compare_text: "甲方：国能长源随州发电有限公司\n乙方：国能日新科技股份有限公司",
  original_snippet: "甲方：国能长源随州发电有限公司随县分公司",
  compare_snippet: "甲方：国能长源随州发电有限公司",
  readable_change: "签署主体变化：甲方：国能长源随州发电有限公司随县分公司 → 甲方：国能长源随州发电有限公司",
  source_type: "signing_region",
  review_flags: ["SIGNING_PARTY_CHANGE", "CRITICAL_VALUE_CHANGE"],
  review_status: "UNREVIEWED",
  original_evidence: [
    {
      page_no: 53,
      bbox: { x0: 71, y0: 86, x1: 287, y1: 137 },
      method: "signing_region",
      text: "甲方：国能长源随州发电有限公司随县分公司",
      highlight_type: "MODIFY",
    },
    {
      page_no: 53,
      bbox: { x0: 50, y0: 78, x1: 542, y1: 537 },
      method: "signing_region",
      text: "签署区整区变化",
    },
  ],
  compare_evidence: [
    {
      page_no: 53,
      bbox: { x0: 89, y0: 101, x1: 287, y1: 128 },
      method: "signing_region",
      text: "甲方：国能长源随州发电有限公司",
      highlight_type: "MODIFY",
    },
    {
      page_no: 53,
      bbox: { x0: 72, y0: 66, x1: 514, y1: 542 },
      method: "signing_region",
      text: "签署区整区变化",
    },
  ],
};

const signingFlagOnlyDiff: DiffItem = {
  ...signingRegionDiff,
  diff_id: "diff-signing-flag",
  source_type: "metadata",
  review_flags: ["SIGNING_DATE_CHANGE"],
};

const mockQuality = {
  task_id: "task-1",
  status: "COMPLETED" as const,
  diff_count: 3,
  review_stats: {
    reviewed_count: 0,
    confirmed_count: 0,
    false_positive_count: 0,
    manual_review_count: 0,
    ignored_count: 0,
  },
  source_counts: { clause: 3 },
  evidence_quality_counts: { HIGH: 2, MEDIUM: 0, LOW: 1 },
  document_profile_summary: {},
  parse_warning_details: [
    { code: "LOW_TEXT_PAGE", message: "文本量较低", severity: "WARNING" as const, source: "document_profiler" },
  ],
  low_confidence_diffs: [
    {
      diff_id: "diff-1",
      title: "付款",
      diff_type: "MODIFY" as const,
      source_type: "clause",
      match_score: 62,
      match_method: "same_clause_no_low_similarity",
      review_flags: ["SAME_CLAUSE_NO_LOW_SIMILARITY"],
      review_status: "UNREVIEWED" as const,
    },
  ],
  low_similarity_diffs: [
    {
      diff_id: "diff-1",
      title: "付款",
      diff_type: "MODIFY" as const,
      source_type: "clause",
      match_score: 62,
      match_method: "same_clause_no_low_similarity",
      review_flags: ["SAME_CLAUSE_NO_LOW_SIMILARITY"],
      review_status: "UNREVIEWED" as const,
    },
  ],
  debug_artifacts: { clause_matches: "clause_matches.json" },
};

function makeBottomAxisDiff(index: number): DiffItem {
  return {
    diff_id: `bottom-${index}`,
    diff_type: "ADD",
    clause_no: `${index + 1}`,
    title: `底部差异 ${index + 1}`,
    original_text: "",
    compare_text: `新增底部差异 ${index + 1}`,
    original_snippet: "",
    compare_snippet: `新增底部差异 ${index + 1}`,
    readable_change: `新增底部差异 ${index + 1}`,
    source_type: "clause",
    review_status: "UNREVIEWED",
    original_evidence: [],
    compare_evidence: [
      {
        page_no: 1,
        bbox: { x0: 72, y0: 812 + index * 5, x1: 240, y1: 830 + index * 5 },
        method: "block",
        text: `新增底部差异 ${index + 1}`,
        highlight_type: "ADD",
      },
    ],
  };
}

describe("ResultPage", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    mockEventSource.onmessage = null;
    mockEventSource.onerror = null;
    mockEventSource.close.mockClear();
    vi.mocked(getTask).mockReset().mockResolvedValue(mockTask);
    vi.mocked(getDiffs).mockReset().mockResolvedValue(mockDiffs);
  });

  it("renders processing state as a progress ring without visible percent text", async () => {
    vi.mocked(getTask).mockResolvedValueOnce({
      ...mockTask,
      status: "PROCESSING",
      stage: "证据定位中",
      progress_percent: 66,
      report_url: "",
    });

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await screen.findByText("证据定位中");
    expect(screen.getByRole("progressbar", { name: /证据定位中/ })).toHaveAttribute("aria-valuenow", "66");
    expect(screen.queryByText("66%")).not.toBeInTheDocument();
  });

  it("finishes the initial load before opening the SSE stream", async () => {
    let resolveTask!: (task: CompareTask) => void;
    vi.mocked(getTask).mockReturnValueOnce(new Promise((resolve) => { resolveTask = resolve; }));

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    expect(createProgressEventSource).not.toHaveBeenCalled();
    await act(async () => resolveTask({ ...mockTask, status: "PROCESSING", terminal_reason: "NONE" }));
    expect(createProgressEventSource).toHaveBeenCalledTimes(1);
  });

  it("falls back after the first-event timeout and keeps polling after a transient error", async () => {
    vi.useFakeTimers();
    const processingTask = {
      ...mockTask,
      status: "PROCESSING" as const,
      terminal_reason: "NONE" as const,
      stage: "证据定位中",
      progress_percent: 66,
      report_url: "",
    };
    vi.mocked(getTask)
      .mockResolvedValueOnce(processingTask)
      .mockRejectedValueOnce(new Error("temporary network error"))
      .mockResolvedValueOnce({ ...processingTask, stage: "报告生成中", progress_percent: 88 });

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    await act(async () => { vi.advanceTimersByTime(3000); await Promise.resolve(); });
    expect(mockEventSource.close).toHaveBeenCalled();

    await act(async () => { vi.advanceTimersByTime(5000); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(2400); await Promise.resolve(); await Promise.resolve(); });
    expect(getTask).toHaveBeenCalledTimes(3);
    expect(screen.getByText("报告生成中")).toBeInTheDocument();
  });

  it("treats a terminal SSE event as a hint until the persisted revision catches up", async () => {
    vi.useFakeTimers();
    const processingTask = {
      ...mockTask,
      status: "PROCESSING" as const,
      terminal_reason: "NONE" as const,
      revision: 4,
      stage: "证据定位中",
      progress_percent: 66,
      report_url: "",
    };
    vi.mocked(getTask)
      .mockResolvedValueOnce(processingTask)
      .mockResolvedValueOnce({ ...mockTask, revision: 4 })
      .mockResolvedValueOnce({ ...mockTask, revision: 5 });
    vi.mocked(getDiffs).mockResolvedValueOnce(mockDiffs);

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      mockEventSource.onmessage?.({ data: JSON.stringify({
        task_id: "task-1", stage: "已完成", progress_percent: 100, status: "COMPLETED", revision: 5,
      }) } as MessageEvent);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.queryByLabelText("原版PDF 在线预览")).not.toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(1200); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(1200); await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByLabelText("原版PDF 在线预览")).toBeInTheDocument();
  });

  it("renders cancellation distinctly and never offers retry", async () => {
    vi.mocked(getTask).mockResolvedValueOnce({
      ...mockTask,
      status: "FAILED",
      terminal_reason: "CANCELLED",
      stage: "已取消",
      errors: ["任务已取消。"],
    });

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "已取消" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("offers retry for an execution failure even when a legacy response omits retry_eligible", async () => {
    vi.mocked(getTask).mockResolvedValueOnce({
      ...mockTask,
      status: "FAILED",
      terminal_reason: "EXECUTION_FAILED",
      retry_eligible: undefined,
      stage: "执行失败",
      errors: [],
    });

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("only offers retry for a submission failure when the backend marks it eligible", async () => {
    vi.mocked(getTask).mockResolvedValueOnce({
      ...mockTask,
      status: "FAILED",
      terminal_reason: "SUBMISSION_FAILED",
      retry_eligible: false,
      stage: "提交失败",
      errors: [],
    });

    const { rerender } = render(<ResultPage taskId="task-ineligible" onBack={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "提交失败" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();

    vi.mocked(getTask).mockResolvedValueOnce({ ...mockTask, status: "FAILED", terminal_reason: "SUBMISSION_FAILED", retry_eligible: true });
    rerender(<ResultPage taskId="task-eligible" onBack={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("converges legacy terminal payloads when both event and task omit revision", async () => {
    vi.useFakeTimers();
    const processing = { ...mockTask, status: "PROCESSING" as const, revision: undefined, report_url: "" };
    const terminal = { ...mockTask, revision: undefined };
    vi.mocked(getTask).mockResolvedValueOnce(processing).mockResolvedValueOnce(terminal);

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      mockEventSource.onmessage?.({ data: JSON.stringify({ task_id: "task-1", status: "COMPLETED", stage: "已完成", progress_percent: 100 }) } as MessageEvent);
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => { vi.advanceTimersByTime(1200); await Promise.resolve(); await Promise.resolve(); });

    expect(screen.getByLabelText("原版PDF 在线预览")).toBeInTheDocument();
  });

  it("does not converge when only the terminal event has a revision", async () => {
    vi.useFakeTimers();
    const processing = { ...mockTask, status: "PROCESSING" as const, revision: undefined, report_url: "" };
    const terminal = { ...mockTask, revision: undefined };
    vi.mocked(getTask).mockResolvedValueOnce(processing).mockResolvedValue(terminal);

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      mockEventSource.onmessage?.({ data: JSON.stringify({ task_id: "task-1", status: "COMPLETED", stage: "已完成", progress_percent: 100, revision: 5 }) } as MessageEvent);
      await Promise.resolve();
    });
    await act(async () => { vi.advanceTimersByTime(5000); await Promise.resolve(); await Promise.resolve(); });

    expect(screen.queryByLabelText("原版PDF 在线预览")).not.toBeInTheDocument();
  });

  it("keeps the first-event timeout armed after a malformed SSE frame", async () => {
    vi.useFakeTimers();
    vi.mocked(getTask)
      .mockResolvedValueOnce({ ...mockTask, status: "PROCESSING", report_url: "" })
      .mockResolvedValueOnce({ ...mockTask, status: "PROCESSING", stage: "轮询恢复", report_url: "" });

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); mockEventSource.onmessage?.({ data: "{" } as MessageEvent); });
    await act(async () => { vi.advanceTimersByTime(4200); await Promise.resolve(); await Promise.resolve(); });

    expect(getTask).toHaveBeenCalledTimes(2);
    expect(screen.getByText("轮询恢复")).toBeInTheDocument();
  });

  it("aborts a pending final diff read when unmounted", async () => {
    let diffSignal: AbortSignal | undefined;
    vi.mocked(getTask).mockResolvedValueOnce(mockTask);
    vi.mocked(getDiffs).mockImplementationOnce(async (_taskId, signal) => {
      diffSignal = signal;
      return await new Promise<DiffItem[]>(() => undefined);
    });
    const { unmount } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    unmount();

    expect(diffSignal?.aborted).toBe(true);
  });

  it("clears the first-event fallback timer when unmounted", async () => {
    vi.useFakeTimers();
    vi.mocked(getTask).mockResolvedValueOnce({ ...mockTask, status: "PROCESSING", report_url: "" });
    const { unmount } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    unmount();
    await act(async () => { vi.advanceTimersByTime(20_000); await Promise.resolve(); });

    expect(getTask).toHaveBeenCalledTimes(1);
    expect(mockEventSource.close).toHaveBeenCalledTimes(1);
  });

  it("clears the completion animation timer without late reads when unmounted", async () => {
    vi.useFakeTimers();
    vi.mocked(getTask)
      .mockResolvedValueOnce({ ...mockTask, status: "PROCESSING", revision: 4, report_url: "" })
      .mockResolvedValueOnce({ ...mockTask, revision: 5 });
    vi.mocked(getDiffs).mockResolvedValueOnce(mockDiffs);
    const { unmount } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      mockEventSource.onmessage?.({ data: JSON.stringify({ task_id: "task-1", status: "COMPLETED", stage: "已完成", progress_percent: 100, revision: 5 }) } as MessageEvent);
      await Promise.resolve();
      await Promise.resolve();
    });

    unmount();
    await act(async () => { vi.advanceTimersByTime(20_000); await Promise.resolve(); });

    expect(getTask).toHaveBeenCalledTimes(2);
    expect(getDiffs).toHaveBeenCalledTimes(1);
  });

  it("closes the stream and aborts in-flight task reads on unmount", async () => {
    vi.mocked(getTask).mockResolvedValueOnce({ ...mockTask, status: "PROCESSING", terminal_reason: "NONE" });
    const { unmount } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const initialSignal = vi.mocked(getTask).mock.calls[0]?.[1];
    unmount();

    expect(mockEventSource.close).toHaveBeenCalled();
    expect(initialSignal?.aborted).toBe(true);
  });

  it("keeps the progress ring visible briefly when SSE completes before rendering results", async () => {
    vi.useFakeTimers();
    vi.mocked(getTask)
      .mockResolvedValueOnce({
        ...mockTask,
        status: "PROCESSING",
        stage: "证据定位中",
        progress_percent: 66,
        report_url: "",
      })
      .mockResolvedValueOnce(mockTask);
    vi.mocked(getDiffs).mockResolvedValueOnce(mockDiffs);

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("证据定位中")).toBeInTheDocument();

    await act(async () => {
      mockEventSource.onmessage?.({
        data: JSON.stringify({
          task_id: "task-1",
          stage: "已完成",
          progress_percent: 100,
          status: "COMPLETED",
        }),
      } as MessageEvent);
    });

    expect(screen.getByText("收尾完成中")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /收尾完成中/ })).toHaveAttribute("aria-valuenow", "100");
    expect(screen.queryByLabelText("原版PDF 在线预览")).not.toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(1200);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByLabelText("原版PDF 在线预览")).toBeInTheDocument();
  });

  it("renders only the PDF.js comparison workspace", async () => {
    const { container } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByLabelText("原版PDF 在线预览")).toBeInTheDocument());
    expect(screen.getByLabelText("原版PDF 在线预览")).toHaveAttribute("data-src", "http://api.test/api/compare/task-1/original");
    expect(screen.getByLabelText("新版PDF 在线预览")).toHaveAttribute("data-src", "http://api.test/api/compare/task-1/compare");
    expect(screen.getByRole("button", { name: "下载原版文件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载新版文件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出报告" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "缩小预览" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "放大预览" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "定位新增改动 diff-1:ADD" })).toBeInTheDocument();
    expect(container.querySelector(".audit-panel")).toHaveClass("is-closed");
    expect(container.querySelector(".audit-panel")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument();
    expect(screen.queryByText("差异审查结果")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "审查详情" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "下载报告" })).not.toBeInTheDocument();
  });

  it("updates real preview zoom controls", async () => {
    const user = userEvent.setup();
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByLabelText("原版PDF 在线预览")).toBeInTheDocument());
    expect(screen.getAllByText("100%").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "放大预览" }));

    expect(screen.getAllByText("110%").length).toBeGreaterThan(0);
  });

  it("expands compare preview layout when original PDF is hidden", async () => {
    const user = userEvent.setup();
    const { container } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByLabelText("原版PDF 在线预览")).toBeInTheDocument());
    expect(container.querySelector(".pdf-compare")).toHaveClass("original-visible");

    await user.click(screen.getByRole("button", { name: "隐藏原版" }));

    expect(container.querySelector(".pdf-compare")).toHaveClass("original-hidden");
    expect(screen.getByLabelText("原版PDF 在线预览")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "显示原版" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "显示原版" }));

    expect(container.querySelector(".pdf-compare")).toHaveClass("original-visible");
  });

  it("downloads the audit analysis report from the task report url", async () => {
    const user = userEvent.setup();
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "导出报告" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "导出报告" }));

    expect(downloadAuthenticatedFile).toHaveBeenCalledWith(
      "http://api.test/api/compare/task-1/report",
      "销售合同差异分析报告.pdf",
    );
  });

  it("orders comparison axis markers by audit item position and colors them by audit type", async () => {
    const user = userEvent.setup();
    const { container } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "定位新增改动 diff-2:ADD" })).toBeInTheDocument());

    const topAddMarker = screen.getByRole("button", { name: "定位新增改动 diff-2:ADD" });
    const mixedAddMarker = screen.getByRole("button", { name: "定位新增改动 diff-1:ADD" });
    const mixedModifyMarker = screen.getByRole("button", { name: "定位修改改动 diff-1:MODIFY" });
    const deleteMarker = screen.getByRole("button", { name: "定位删除改动 diff-3:DELETE" });
    const markers = Array.from(container.querySelectorAll(".compare-axis .axis-marker"));

    expect(markers).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "定位删除改动 diff-4:DELETE" })).not.toBeInTheDocument();
    expect(topAddMarker).toHaveClass("add");
    expect(mixedAddMarker).toHaveClass("add");
    expect(mixedModifyMarker).toHaveClass("modify");
    expect(deleteMarker).toHaveClass("delete");
    expect(parseFloat(topAddMarker.style.top)).toBeLessThan(parseFloat(mixedAddMarker.style.top));
    expect(parseFloat(mixedAddMarker.style.top)).toBeLessThan(parseFloat(mixedModifyMarker.style.top));
    expect(parseFloat(mixedModifyMarker.style.top)).toBeLessThan(parseFloat(deleteMarker.style.top));

    await user.click(mixedModifyMarker);

    expect(mixedModifyMarker).toHaveClass("active");
  });

  it("spreads clustered comparison axis markers away from the bottom boundary", async () => {
    vi.mocked(getDiffs).mockResolvedValueOnce(Array.from({ length: 6 }, (_, index) => makeBottomAxisDiff(index)));
    const { container } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "定位新增改动 bottom-0:ADD" })).toBeInTheDocument());

    const tops = Array.from(container.querySelectorAll<HTMLButtonElement>(".compare-axis .axis-marker"))
      .map((marker) => parseFloat(marker.style.top));

    expect(tops).toHaveLength(6);
    expect(Math.min(...tops)).toBeGreaterThanOrEqual(3);
    expect(Math.max(...tops)).toBeLessThanOrEqual(97);
    for (let index = 1; index < tops.length; index += 1) {
      expect(tops[index] - tops[index - 1]).toBeGreaterThanOrEqual(3.99);
    }
  });

  it("filters audit panel items and marks the selected diff", async () => {
    const user = userEvent.setup();
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(screen.getByRole("button", { name: "筛选全部差异" })).toHaveTextContent("4");
    expect(screen.getByRole("button", { name: "筛选删除差异" })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "筛选新增差异" })).toHaveTextContent("2");
    expect(screen.getByRole("button", { name: "筛选修改差异" })).toHaveTextContent("1");
    expect(screen.getByLabelText("正文差异")).toBeInTheDocument();
    expect(screen.getByLabelText("结构与质量提示")).toBeInTheDocument();
    expect(await screen.findAllByText("低置信 OCR")).not.toHaveLength(0);
    expect(screen.getAllByText("页面不可靠")).not.toHaveLength(0);
    expect(screen.getAllByText("表格识别风险")).not.toHaveLength(0);
    expect(screen.getAllByText("版面匹配风险")).not.toHaveLength(0);
    expect(screen.getAllByText("阅读顺序风险")).not.toHaveLength(0);
    expect(screen.getAllByText("签章识别风险")).not.toHaveLength(0);
    expect(screen.getAllByText("证据不可靠")).not.toHaveLength(0);
    expect(await screen.findAllByText("处置规划")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "审计定位改动 diff-4:DELETE" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "筛选新增差异" }));

    expect(screen.getByRole("button", { name: "审计定位改动 diff-1:ADD" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审计定位改动 diff-2:ADD" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审计定位改动 diff-3:DELETE" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审计定位改动 diff-1:ADD" })).toHaveTextContent("新增");

    const addCard = screen.getByRole("button", { name: "审计定位改动 diff-2:ADD" });
    await user.click(addCard);

    expect(addCard.closest(".audit-diff-card")).toHaveClass("active");

    await user.click(screen.getByRole("button", { name: "筛选修改差异" }));

    expect(screen.getByRole("button", { name: "审计定位改动 diff-1:MODIFY" })).toHaveTextContent("修改");
    expect(screen.queryByRole("button", { name: "审计定位改动 diff-2:ADD" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "筛选删除差异" }));

    expect(screen.getByRole("button", { name: "审计定位改动 diff-3:DELETE" })).toHaveTextContent("删除");
    expect(screen.queryByRole("button", { name: "审计定位改动 diff-1:ADD" })).not.toBeInTheDocument();
  });

  it("shows a whole metadata replacement as one modify audit item", async () => {
    const user = userEvent.setup();
    vi.mocked(getDiffs).mockResolvedValueOnce([preambleReplacementDiff]);
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(screen.getByRole("button", { name: "筛选全部差异" })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "筛选新增差异" })).toHaveTextContent("0");
    expect(screen.getByRole("button", { name: "筛选删除差异" })).toHaveTextContent("0");
    expect(screen.getByRole("button", { name: "筛选修改差异" })).toHaveTextContent("1");
    const modifyCard = screen.getByRole("button", { name: "审计定位改动 diff-preamble-title:MODIFY" });
    expect(modifyCard).toHaveTextContent(
      "原文：国能长源随州发电有限公司随县分公司 2026年新能源场站功率预测系统授权服务合同",
    );
    expect(modifyCard).toHaveTextContent(
      "修改后：长源电力随州公司2026年新能源场站 功率预测系统授权服务单一来源项目合同",
    );
    expect(screen.queryByRole("button", { name: "审计定位改动 diff-preamble-title:ADD" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审计定位改动 diff-preamble-title:DELETE" })).not.toBeInTheDocument();
  });

  it("groups signing region differences between main and structural audit groups", async () => {
    const user = userEvent.setup();
    vi.mocked(getDiffs).mockResolvedValueOnce([mockDiffs[0], signingRegionDiff, mockDiffs[1]]);
    const { container } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    const signingGroup = screen.getByLabelText("签章区差异");
    expect(within(signingGroup).getByRole("button", { name: "审计定位改动 diff-signing:ADD" })).toBeInTheDocument();
    expect(
      within(screen.getByLabelText("结构与质量提示")).queryByRole("button", { name: "审计定位改动 diff-signing:ADD" }),
    ).not.toBeInTheDocument();
    expect(Array.from(container.querySelectorAll(".audit-diff-group-head > strong")).map((node) => node.textContent)).toEqual([
      "正文差异",
      "签章区差异",
      "结构与质量提示",
    ]);
  });

  it("shows a wrapped signing party replacement as one complete modify item", async () => {
    const user = userEvent.setup();
    vi.mocked(getDiffs).mockResolvedValueOnce([signingPartyModifyDiff]);
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(screen.getByRole("button", { name: "筛选新增差异" })).toHaveTextContent("0");
    expect(screen.getByRole("button", { name: "筛选删除差异" })).toHaveTextContent("0");
    expect(screen.getByRole("button", { name: "筛选修改差异" })).toHaveTextContent("1");
    const modifyCard = screen.getByRole("button", { name: "审计定位改动 diff-signing-party:MODIFY" });
    expect(modifyCard).toHaveTextContent("原文：甲方：国能长源随州发电有限公司随县分公司");
    expect(modifyCard).toHaveTextContent("修改后：甲方：国能长源随州发电有限公司");
    expect(modifyCard).toHaveTextContent("关键差异");
  });

  it("groups signing review flags as signing region differences", async () => {
    const user = userEvent.setup();
    vi.mocked(getDiffs).mockResolvedValueOnce([mockDiffs[0], signingFlagOnlyDiff, mockDiffs[1]]);
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(
      within(screen.getByLabelText("签章区差异")).getByRole("button", { name: "审计定位改动 diff-signing-flag:ADD" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByLabelText("结构与质量提示")).queryByRole("button", { name: "审计定位改动 diff-signing-flag:ADD" }),
    ).not.toBeInTheDocument();
  });

  it("uses the highest-severity remediation action for every audit card in a diff", async () => {
    const user = userEvent.setup();
    vi.mocked(getTask).mockResolvedValueOnce({
      ...mockTask,
      ocr_remediation_summary: {
        ...mockTask.ocr_remediation_summary!,
        actions: [
          {
            action_id: "original:1:diff-1:RELOCATE_EVIDENCE:SUCCEEDED",
            action_type: "RELOCATE_EVIDENCE",
            reason: "EVIDENCE_UNRELIABLE",
            status: "SUCCEEDED",
            side: "original",
            page_no: 1,
            diff_id: "diff-1",
            before_quality: {},
            after_quality: {},
            changed_evidence: false,
            changed_diff_text: false,
            review_flags_added: [],
            notes: [],
          },
          {
            action_id: "original:1:diff-1:RELOCATE_EVIDENCE:FAILED",
            action_type: "RELOCATE_EVIDENCE",
            reason: "EVIDENCE_UNRELIABLE",
            status: "FAILED",
            side: "original",
            page_no: 1,
            diff_id: "diff-1",
            before_quality: {},
            after_quality: {},
            changed_evidence: false,
            changed_diff_text: false,
            review_flags_added: [],
            notes: [],
          },
        ],
      },
    });

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(await screen.findAllByText("处置未完成")).toHaveLength(2);
    expect(screen.queryByText("已自动处置")).not.toBeInTheDocument();
  });

  it("shows successful remediation badges for every audit card in a remediated diff", async () => {
    const user = userEvent.setup();
    vi.mocked(getTask).mockResolvedValueOnce({
      ...mockTask,
      ocr_remediation_summary: {
        ...mockTask.ocr_remediation_summary!,
        actions: [
          {
            action_id: "original:1:diff-1:RELOCATE_EVIDENCE:SUCCEEDED",
            action_type: "RELOCATE_EVIDENCE",
            reason: "EVIDENCE_UNRELIABLE",
            status: "SUCCEEDED",
            side: "original",
            page_no: 1,
            diff_id: "diff-1",
            before_quality: { max_confidence: 0.46 },
            after_quality: { max_confidence: 0.98 },
            changed_evidence: true,
            changed_diff_text: false,
            review_flags_added: ["OCR_REMEDIATION_EVIDENCE_RELOCATED"],
            notes: [],
          },
        ],
      },
    });

    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(await screen.findAllByText("已自动处置")).toHaveLength(2);
    expect(screen.queryByText("处置规划")).not.toBeInTheDocument();
  });

  it.each([
    ["missing", (() => {
      const { ocr_remediation_summary: _summary, ...taskWithoutSummary } = mockTask;
      return taskWithoutSummary;
    })()],
    ["null", { ...mockTask, ocr_remediation_summary: null }],
    ["empty", { ...mockTask, ocr_remediation_summary: { ...mockTask.ocr_remediation_summary!, actions: [] } }],
  ] satisfies Array<[string, CompareTask]>)(
    "does not render remediation badges when the remediation summary is %s",
    async (_caseName, taskPayload) => {
      const user = userEvent.setup();
      vi.mocked(getTask).mockResolvedValueOnce(taskPayload);

      render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

      await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
      await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

      expect(screen.queryByText("处置规划")).not.toBeInTheDocument();
      expect(screen.queryByText("需人工处置")).not.toBeInTheDocument();
      expect(screen.queryByText("已自动处置")).not.toBeInTheDocument();
      expect(screen.queryByText("处置未完成")).not.toBeInTheDocument();
    },
  );

  it("submits an ignored review decision from the audit panel", async () => {
    const user = userEvent.setup();
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(screen.queryByLabelText("质量诊断摘要")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("复核意见 diff-1")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认 diff-1" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "误报 diff-1" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "待确认 diff-1" })).not.toBeInTheDocument();
    expect(screen.queryByText("封面")).not.toBeInTheDocument();
    expect(screen.queryByText(/证据高/)).not.toBeInTheDocument();
    expect(screen.queryByText("未复核")).not.toBeInTheDocument();
    expect(screen.queryByText("同编号低相似")).not.toBeInTheDocument();

    const ignoreButton = screen.getByRole("button", { name: "忽略 diff-1:ADD" });
    const auditCard = ignoreButton.closest(".audit-diff-card");
    await user.click(ignoreButton);

    await waitFor(() => expect(updateAuditItemReview).toHaveBeenCalled());
    expect(updateAuditItemReview).toHaveBeenCalledWith("task-1", "diff-1:ADD", {
      review_status: "IGNORED",
      review_comment: "",
    });
    await waitFor(() => expect(auditCard).toHaveClass("ignored"));
    expect(screen.getByRole("button", { name: "恢复 diff-1:ADD" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "恢复 diff-1:ADD" }));

    await waitFor(() =>
      expect(updateAuditItemReview).toHaveBeenLastCalledWith("task-1", "diff-1:ADD", {
        review_status: "UNREVIEWED",
        review_comment: "",
      }),
    );
    await waitFor(() => expect(auditCard).not.toHaveClass("ignored"));
    expect(screen.getByRole("button", { name: "忽略 diff-1:ADD" })).toBeInTheDocument();
  });

  it("collapses and reopens the audit panel", async () => {
    const user = userEvent.setup();
    const { container } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    expect(container.querySelector(".audit-panel")).toHaveClass("is-closed");
    expect(container.querySelector(".audit-panel")).toHaveAttribute("aria-hidden", "true");

    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(container.querySelector(".audit-panel")).toHaveClass("is-open");

    await user.click(screen.getByRole("button", { name: "收起审计侧栏" }));

    expect(container.querySelector(".audit-panel")).toHaveClass("is-closed");
    expect(container.querySelector(".audit-panel")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(screen.getByLabelText("审计统计侧栏")).toBeInTheDocument();
    expect(container.querySelector(".audit-panel")).toHaveClass("is-open");
  });
});
