import { describe, expect, it } from "vitest";

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
