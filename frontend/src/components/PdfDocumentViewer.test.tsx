import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { DiffItem } from "../types";
import { getPageHighlights, highlightStyle, PdfHighlightLayer } from "./PdfDocumentViewer";
import { getCurrentPageFromScroll } from "./pdfPageScroll";

const pages = [
  { pageNumber: 1, offsetTop: 0, offsetHeight: 800 },
  { pageNumber: 2, offsetTop: 820, offsetHeight: 800 },
  { pageNumber: 3, offsetTop: 1640, offsetHeight: 800 },
  { pageNumber: 4, offsetTop: 2460, offsetHeight: 800 },
];

describe("getCurrentPageFromScroll", () => {
  it("returns the first page when the viewport center is on page one", () => {
    expect(getCurrentPageFromScroll(0, 600, pages)).toBe(1);
  });

  it("returns the page containing the viewport center", () => {
    expect(getCurrentPageFromScroll(1500, 600, pages)).toBe(3);
  });

  it("keeps the page inside the available page range", () => {
    expect(getCurrentPageFromScroll(9999, 600, pages)).toBe(4);
    expect(getCurrentPageFromScroll(0, 600, [])).toBe(1);
  });
});

const diff: DiffItem = {
  diff_id: "diff-1",
  diff_type: "MODIFY",
  clause_no: "1",
  title: "付款",
  original_text: "付款期限为30天。",
  compare_text: "付款期限为45天，新增说明。",
  original_snippet: "30",
  compare_snippet: "45，新增说明",
  readable_change: "原文：30\n修改后：45，新增说明",
  ai_analysis: null,
  original_screenshot: "",
  compare_screenshot: "",
  original_evidence: [
    {
      page_no: 1,
      bbox: { x0: 10, y0: 20, x1: 70, y1: 40 },
      method: "text_exact",
      text: "30",
      highlight_type: "MODIFY",
    },
  ],
  compare_evidence: [
    {
      page_no: 1,
      bbox: { x0: 10, y0: 20, x1: 70, y1: 40 },
      method: "text_exact",
      text: "45",
      highlight_type: "MODIFY",
    },
    {
      page_no: 2,
      bbox: { x0: 80, y0: 120, x1: 180, y1: 150 },
      method: "text_exact",
      text: "新增说明",
      highlight_type: "ADD",
    },
    {
      page_no: 2,
      bbox: { x0: 186, y0: 121, x1: 220, y1: 151 },
      method: "text_exact",
      text: "补充",
      highlight_type: "ADD",
    },
  ],
};

const fallbackDiff: DiffItem = {
  ...diff,
  diff_id: "diff-2",
  diff_type: "DELETE",
  original_evidence: [
    {
      page_no: 1,
      bbox: { x0: 30, y0: 100, x1: 500, y1: 180 },
      method: "block_fallback",
      text: "整块条款",
      highlight_type: "DELETE",
    },
  ],
  compare_evidence: [],
};

describe("PDF diff highlights", () => {
  it("filters evidence by side and page", () => {
    const originalHighlights = getPageHighlights([diff], "original", 1);
    const comparePageOneHighlights = getPageHighlights([diff], "compare", 1);
    const comparePageTwoHighlights = getPageHighlights([diff], "compare", 2);

    expect(originalHighlights).toHaveLength(1);
    expect(originalHighlights[0].evidence.text).toBe("30");
    expect(comparePageOneHighlights).toHaveLength(1);
    expect(comparePageOneHighlights[0].evidence.text).toBe("45");
    expect(comparePageTwoHighlights).toHaveLength(1);
    expect(comparePageTwoHighlights[0].type).toBe("ADD");
    expect(comparePageTwoHighlights[0].evidence.text).toBe("新增说明 补充");
  });

  it("scales highlight coordinates with zoom", () => {
    const highlight = getPageHighlights([diff], "original", 1)[0];
    expect(highlightStyle(highlight, 1.5)).toEqual({
      left: "15px",
      top: "56.25px",
      width: "90px",
      height: "3.75px",
    });
  });

  it("renders typed underline highlights and activates the owning diff", async () => {
    const user = userEvent.setup();
    const onActivateDiff = vi.fn();
    const highlights = getPageHighlights([diff], "compare", 2);

    render(<PdfHighlightLayer activeDiffId="diff-1" highlights={highlights} zoom={1} onActivateDiff={onActivateDiff} />);

    const box = screen.getByRole("button", { name: "定位差异 diff-1" });
    expect(box).toHaveClass("pdf-highlight-box", "add", "underline", "active");
    expect(box).toHaveStyle({ left: "80px", top: "148.5px", width: "140px", height: "2.5px" });

    await user.click(box);

    expect(onActivateDiff).toHaveBeenCalledWith("diff-1");
  });

  it("does not render page highlights before a diff is active", () => {
    const highlights = getPageHighlights([diff], "compare", 2);

    const { container } = render(<PdfHighlightLayer activeDiffId="" highlights={highlights} zoom={1} onActivateDiff={vi.fn()} />);

    expect(container.querySelector(".pdf-highlight-box")).not.toBeInTheDocument();
  });

  it("renders only active diff highlights", () => {
    const highlights = getPageHighlights([diff, fallbackDiff], "original", 1);

    render(<PdfHighlightLayer activeDiffId="diff-2" highlights={highlights} zoom={1} onActivateDiff={vi.fn()} />);

    expect(screen.getByRole("button", { name: "定位差异 diff-2" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定位差异 diff-1" })).not.toBeInTheDocument();
  });

  it("renders fallback evidence as a locator rail instead of a filled block", () => {
    const highlight = getPageHighlights([fallbackDiff], "original", 1)[0];

    render(<PdfHighlightLayer activeDiffId="diff-2" highlights={[highlight]} zoom={1} onActivateDiff={vi.fn()} />);

    const box = screen.getByRole("button", { name: "定位差异 diff-2" });
    expect(box).toHaveClass("pdf-highlight-box", "delete", "fallback", "active");
    expect(box).toHaveStyle({ left: "22px", top: "100px", width: "5px", height: "80px" });
  });
});
