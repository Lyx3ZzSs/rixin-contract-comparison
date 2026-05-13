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
  diff_count: 1,
  high_risk_count: 1,
  medium_risk_count: 0,
  low_risk_count: 0,
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
];

describe("ResultPage", () => {
  it("renders only the PDF.js comparison workspace", async () => {
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByLabelText("原版PDF 在线预览")).toBeInTheDocument());
    expect(screen.getByLabelText("原版PDF 在线预览")).toHaveAttribute("data-src", "http://api.test/api/compare/task-1/original");
    expect(screen.getByLabelText("新版PDF 在线预览")).toHaveAttribute("data-src", "http://api.test/api/compare/task-1/compare");
    expect(screen.getByRole("button", { name: "下载原版文件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载新版文件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "缩小预览" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "放大预览" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "定位差异 diff-1" })).toHaveLength(1);
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
});
