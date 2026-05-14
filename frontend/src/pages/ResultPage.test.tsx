import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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
  toApiUrl: (path: string) => `http://api.test${path}`,
}));

const mockTask: CompareTask = {
  task_id: "task-1",
  status: "COMPLETED",
  created_at: "2026-05-12T00:00:00Z",
  updated_at: "2026-05-12T00:00:10Z",
  original_filename: "original.pdf",
  compare_filename: "compare.pdf",
  diff_count: 3,
  high_risk_count: 1,
  medium_risk_count: 1,
  low_risk_count: 1,
  ai_summary: "付款期限延长，需关注回款风险。",
  report_url: "/api/compare/task-1/report",
  original_pdf_url: "/api/compare/task-1/original",
  compare_pdf_url: "/api/compare/task-1/compare",
  original_highlight_pdf_url: "/api/compare/task-1/highlight/original",
  compare_highlight_pdf_url: "/api/compare/task-1/highlight/compare",
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
    original_screenshot: "",
    compare_screenshot: "",
    original_screenshot_url: "/api/compare/task-1/screenshot/original.png",
    compare_screenshot_url: "/api/compare/task-1/screenshot/compare.png",
    original_evidence: [{ page_no: 1, bbox: { x0: 72, y0: 120, x1: 240, y1: 146 }, method: "block", text: "30 days" }],
    compare_evidence: [{ page_no: 1, bbox: { x0: 72, y0: 120, x1: 240, y1: 146 }, method: "block", text: "45 days" }],
    ai_analysis: {
      risk_level: "HIGH",
      risk_score: 86,
      contract_element: "付款条款",
      change_summary: "付款期限延长。",
      risk_explanation: "可能影响现金流。",
      review_suggestion: "建议业务确认授信周期。",
    },
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
    original_screenshot: "",
    compare_screenshot: "",
    original_evidence: [],
    compare_evidence: [{ page_no: 1, bbox: { x0: 72, y0: 180, x1: 240, y1: 206 }, method: "block", text: "invoice" }],
    ai_analysis: null,
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
    original_screenshot: "",
    compare_screenshot: "",
    original_evidence: [{ page_no: 1, bbox: { x0: 72, y0: 240, x1: 240, y1: 266 }, method: "block", text: "12 months" }],
    compare_evidence: [],
    ai_analysis: null,
  },
];

describe("ResultPage", () => {
  it("renders only the PDF.js comparison workspace", async () => {
    const { container } = render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByLabelText("原版PDF 在线预览")).toBeInTheDocument());
    expect(screen.getByLabelText("原版PDF 在线预览")).toHaveAttribute("data-src", "http://api.test/api/compare/task-1/highlight/original");
    expect(screen.getByLabelText("新版PDF 在线预览")).toHaveAttribute("data-src", "http://api.test/api/compare/task-1/highlight/compare");
    expect(screen.getByRole("button", { name: "下载原版文件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载新版文件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "缩小预览" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "放大预览" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "定位差异 diff-1" })).toHaveLength(1);
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

  it("filters audit panel items and marks the selected diff", async () => {
    const user = userEvent.setup();
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "展开审计侧栏" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "展开审计侧栏" }));

    expect(screen.getByRole("button", { name: "筛选全部差异" })).toHaveTextContent("3");
    expect(screen.getByRole("button", { name: "筛选删除差异" })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "筛选新增差异" })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "筛选修改差异" })).toHaveTextContent("1");
    await user.click(screen.getByRole("button", { name: "筛选新增差异" }));

    expect(screen.getByRole("button", { name: "审计定位差异 diff-2" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审计定位差异 diff-1" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审计定位差异 diff-3" })).not.toBeInTheDocument();

    const addCard = screen.getByRole("button", { name: "审计定位差异 diff-2" });
    await user.click(addCard);

    expect(addCard).toHaveClass("active");
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
