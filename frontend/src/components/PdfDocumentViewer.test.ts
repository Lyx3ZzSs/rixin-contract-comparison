import { describe, expect, it } from "vitest";

import { computeRenderWindow, getCurrentPageFromScroll } from "./pdfPageScroll";

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

describe("computeRenderWindow", () => {
  it("includes pages intersecting the viewport plus the buffer", () => {
    expect(computeRenderWindow(pages, 0, 600, 800)).toEqual({ start: 1, end: 2 });
    expect(computeRenderWindow(pages, 1500, 600, 0)).toEqual({ start: 2, end: 3 });
    expect(computeRenderWindow(pages, 2400, 600, 800)).toEqual({ start: 2, end: 4 });
  });

  it("spans multiple pages when the buffer covers them", () => {
    expect(computeRenderWindow(pages, 820, 600, 800)).toEqual({ start: 1, end: 3 });
  });

  it("falls back to page one when there are no pages", () => {
    expect(computeRenderWindow([], 0, 600, 800)).toEqual({ start: 1, end: 1 });
  });
});
