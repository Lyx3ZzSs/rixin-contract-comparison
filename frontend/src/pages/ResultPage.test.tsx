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
  report_filename: "销售合同差异分析报告.pdf",
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
    original_evidence: [
      {
        page_no: 1,
        bbox: { x0: 72, y0: 120, x1: 240, y1: 146 },
        method: "block",
        text: "30 days",
        highlight_type: "MODIFY",
      },
    ],
    compare_evidence: [
      {
        page_no: 1,
        bbox: { x0: 72, y0: 120, x1: 240, y1: 146 },
        method: "block",
        text: "45 days",
        highlight_type: "MODIFY",
      },
      {
        page_no: 1,
        bbox: { x0: 250, y0: 120, x1: 340, y1: 146 },
        method: "block",
        text: "新增付款说明",
        highlight_type: "ADD",
      },
    ],
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
    compare_evidence: [
      {
        page_no: 1,
        bbox: { x0: 72, y0: 60, x1: 240, y1: 86 },
        method: "block",
        text: "",
        highlight_type: "ADD",
      },
    ],
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
    await user.click(screen.getByRole("button", { name: "筛选新增差异" }));

    expect(screen.getByRole("button", { name: "审计定位改动 diff-1:ADD" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审计定位改动 diff-2:ADD" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审计定位改动 diff-3:DELETE" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审计定位改动 diff-1:ADD" })).toHaveTextContent("新增");

    const addCard = screen.getByRole("button", { name: "审计定位改动 diff-2:ADD" });
    await user.click(addCard);

    expect(addCard).toHaveClass("active");

    await user.click(screen.getByRole("button", { name: "筛选修改差异" }));

    expect(screen.getByRole("button", { name: "审计定位改动 diff-1:MODIFY" })).toHaveTextContent("修改");
    expect(screen.queryByRole("button", { name: "审计定位改动 diff-2:ADD" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "筛选删除差异" }));

    expect(screen.getByRole("button", { name: "审计定位改动 diff-3:DELETE" })).toHaveTextContent("删除");
    expect(screen.queryByRole("button", { name: "审计定位改动 diff-1:ADD" })).not.toBeInTheDocument();
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
