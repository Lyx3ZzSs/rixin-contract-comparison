import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getDiffs, getTask, updateAuditItemReview } from "../lib/api";
import type { CompareTask, DiffItem } from "../types";
import { ResultPage } from "./ResultPage";

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
      reviewed_by: "local_reviewer",
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
    review_flags: ["SAME_CLAUSE_NO_LOW_SIMILARITY"],
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

describe("ResultPage", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    mockEventSource.onmessage = null;
    mockEventSource.onerror = null;
    mockEventSource.close.mockClear();
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
    const fetchMock = vi.fn(async () => new Response(new Blob(["pdf"], { type: "application/pdf" }), { status: 200 }));
    const createObjectUrl = vi.fn(() => "blob:report");
    const revokeObjectUrl = vi.fn();
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe("销售合同差异分析报告.pdf");
    });
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(URL, "createObjectURL", { value: createObjectUrl, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectUrl, configurable: true });
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "导出报告" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "导出报告" }));

    expect(fetchMock).toHaveBeenCalledWith("http://api.test/api/compare/task-1/report");
    expect(createObjectUrl).toHaveBeenCalled();
    expect(anchorClick).toHaveBeenCalled();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:report");
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

  it("filters audit panel items and marks the selected diff", async () => {
    const user = userEvent.setup();
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(screen.getByRole("button", { name: "筛选全部差异" })).toHaveTextContent("4");
    expect(screen.getByRole("button", { name: "筛选删除差异" })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "筛选新增差异" })).toHaveTextContent("2");
    expect(screen.getByRole("button", { name: "筛选修改差异" })).toHaveTextContent("1");
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
      reviewed_by: "local_reviewer",
    });
    await waitFor(() => expect(auditCard).toHaveClass("ignored"));
    expect(screen.getByRole("button", { name: "恢复 diff-1:ADD" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "恢复 diff-1:ADD" }));

    await waitFor(() =>
      expect(updateAuditItemReview).toHaveBeenLastCalledWith("task-1", "diff-1:ADD", {
        review_status: "UNREVIEWED",
        review_comment: "",
        reviewed_by: "local_reviewer",
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
