import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { DiffItem } from "../types";
import { getPageHighlights, highlightRect, PdfHighlightLayer } from "./PdfDocumentViewer";
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

const sealDiff: DiffItem = {
  ...diff,
  diff_id: "diff-seal",
  diff_type: "ADD",
  source_type: "seal",
  compare_evidence: [
    {
      page_no: 1,
      bbox: { x0: 200, y0: 500, x1: 300, y1: 600 },
      method: "seal_region",
      text: "合同专用章",
      highlight_type: "ADD",
    },
  ],
  original_evidence: [],
};

const signingRegionDiff: DiffItem = {
  ...diff,
  diff_id: "diff-signing",
  diff_type: "MODIFY",
  source_type: "signing_region",
  compare_evidence: [
    {
      page_no: 1,
      bbox: { x0: 220, y0: 620, x1: 360, y1: 700 },
      method: "signing_region_element",
      text: "授权代表签字",
      highlight_type: "MODIFY",
    },
  ],
  original_evidence: [],
};

const signingPartyDiff: DiffItem = {
  ...signingRegionDiff,
  diff_id: "diff-signing-party",
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
  compare_evidence: [],
};

const signingPartyAndVisualDiff: DiffItem = {
  ...signingPartyDiff,
  diff_id: "diff-signing-party-and-visual",
  original_evidence: [
    ...(signingPartyDiff.original_evidence ?? []),
    {
      page_no: 53,
      bbox: { x0: 50, y0: 78, x1: 542, y1: 537 },
      method: "signing_region_visual",
      text: "签署区盖章、签字和日期变化",
      highlight_type: "MODIFY",
    },
  ],
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
    expect(highlightRect(highlight, 1.5)).toEqual({
      x: 15,
      y: 30,
      width: 90,
      height: 30,
    });
  });

  it("renders typed highlighter marks and activates the owning diff", async () => {
    const user = userEvent.setup();
    const onActivateDiff = vi.fn();
    const highlights = getPageHighlights([diff], "compare", 2);

    render(
      <PdfHighlightLayer
        activeDiffId="diff-1"
        highlights={highlights}
        pageSize={{ width: 595, height: 842 }}
        zoom={1}
        onActivateDiff={onActivateDiff}
      />,
    );

    const box = screen.getByRole("button", { name: "定位差异 diff-1" });
    expect(box).toHaveClass("pdf-highlight-mark", "add", "text", "active");
    const rect = box.querySelector("rect");
    expect(rect).toHaveAttribute("x", "80");
    expect(rect).toHaveAttribute("y", "120");
    expect(rect).toHaveAttribute("width", "140");
    expect(rect).toHaveAttribute("height", "31");

    await user.click(box);

    expect(onActivateDiff).toHaveBeenCalledWith("diff-1");
  });

  it("renders muted page highlights before a diff is active", () => {
    const highlights = getPageHighlights([diff], "compare", 2);

    render(
      <PdfHighlightLayer
        activeDiffId=""
        highlights={highlights}
        pageSize={{ width: 595, height: 842 }}
        zoom={1}
        onActivateDiff={vi.fn()}
      />,
    );

    const box = screen.getByRole("button", { name: "定位差异 diff-1" });
    expect(box).toHaveClass("pdf-highlight-mark", "add", "text", "muted");
  });

  it("renders all page highlights while strengthening only the active diff", () => {
    const highlights = getPageHighlights([diff, fallbackDiff], "original", 1);

    render(
      <PdfHighlightLayer
        activeDiffId="diff-2"
        highlights={highlights}
        pageSize={{ width: 595, height: 842 }}
        zoom={1}
        onActivateDiff={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "定位差异 diff-2" })).toHaveClass("active");
    expect(screen.getByRole("button", { name: "定位差异 diff-1" })).toHaveClass("muted");
  });

  it("renders fallback evidence as a filled highlighter block", () => {
    const highlight = getPageHighlights([fallbackDiff], "original", 1)[0];

    render(
      <PdfHighlightLayer
        activeDiffId="diff-2"
        highlights={[highlight]}
        pageSize={{ width: 595, height: 842 }}
        zoom={1}
        onActivateDiff={vi.fn()}
      />,
    );

    const box = screen.getByRole("button", { name: "定位差异 diff-2" });
    expect(box).toHaveClass("pdf-highlight-mark", "delete", "fallback", "active");
    const rect = box.querySelector("rect");
    expect(rect).toHaveAttribute("x", "30");
    expect(rect).toHaveAttribute("y", "100");
    expect(rect).toHaveAttribute("width", "470");
    expect(rect).toHaveAttribute("height", "80");
  });

  it("renders seal region evidence with the muted seal mark kind", () => {
    const highlight = getPageHighlights([sealDiff], "compare", 1)[0];

    render(
      <PdfHighlightLayer
        activeDiffId="diff-seal"
        highlights={[highlight]}
        pageSize={{ width: 595, height: 842 }}
        zoom={1}
        onActivateDiff={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "定位差异 diff-seal" })).toHaveClass(
      "pdf-highlight-mark",
      "add",
      "seal",
      "active",
    );
  });

  it.each(["signing_region", "signing_region_element", "signing_region_visual"])(
    "renders %s evidence with the signing region mark kind",
    (method) => {
      const methodDiff: DiffItem = {
        ...signingRegionDiff,
        compare_evidence: signingRegionDiff.compare_evidence?.map((evidence) => ({ ...evidence, method })),
      };
      const highlight = getPageHighlights([methodDiff], "compare", 1)[0];

      render(
        <PdfHighlightLayer
          activeDiffId="diff-signing"
          highlights={[highlight]}
          pageSize={{ width: 595, height: 842 }}
          zoom={1}
          onActivateDiff={vi.fn()}
        />,
      );

      expect(screen.getByRole("button", { name: "定位差异 diff-signing" })).toHaveClass(
        "pdf-highlight-mark",
        "modify",
        "signing-region",
        "active",
      );
    },
  );

  it("uses typed signing party evidence instead of the untyped whole-region context", () => {
    const highlights = getPageHighlights([signingPartyDiff], "original", 53);

    expect(highlights).toHaveLength(1);
    expect(highlights[0].type).toBe("MODIFY");
    expect(highlights[0].evidence.text).toBe("甲方：国能长源随州发电有限公司随县分公司");
    expect(highlights[0].evidence.bbox).toEqual({ x0: 71, y0: 86, x1: 287, y1: 137 });
  });

  it("uses the full signing region without a nested party box when visual changes accompany a party change", () => {
    const highlights = getPageHighlights([signingPartyAndVisualDiff], "original", 53);

    expect(highlights).toHaveLength(1);
    expect(highlights[0].type).toBe("MODIFY");
    expect(highlights[0].evidence.method).toBe("signing_region_visual");
    expect(highlights[0].evidence.bbox).toEqual({ x0: 50, y0: 78, x1: 542, y1: 537 });
  });
});
