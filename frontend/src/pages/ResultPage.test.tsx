import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CompareTask, DiffItem } from "../types";
import { ResultPage } from "./ResultPage";

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
  it("renders stats, PDF URLs, diff details, screenshots, and report link", async () => {
    render(<ResultPage taskId="task-1" onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("差异审查结果")).toBeInTheDocument());
    expect(screen.getAllByText("高风险").length).toBeGreaterThan(0);
    expect(screen.getByText("付款期限由 30 天调整为 45 天。")).toBeInTheDocument();
    expect(screen.getByText("付款条款")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载报告" })).toHaveAttribute(
      "href",
      "http://api.test/api/compare/task-1/report",
    );
    expect(screen.getByTitle("原合同高亮 PDF")).toHaveAttribute(
      "src",
      "http://api.test/api/compare/task-1/highlight/original",
    );
    expect(screen.getByAltText("原合同差异截图")).toHaveAttribute(
      "src",
      "http://api.test/api/compare/task-1/screenshot/original.png",
    );
  });
});
