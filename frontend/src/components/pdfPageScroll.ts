export interface PageScrollMetric {
  pageNumber: number;
  offsetTop: number;
  offsetHeight: number;
}

export function getCurrentPageFromScroll(
  scrollTop: number,
  viewportHeight: number,
  pages: PageScrollMetric[],
): number {
  if (pages.length === 0) {
    return 1;
  }

  const viewportCenter = scrollTop + viewportHeight / 2;
  const containingPage = pages.find(
    (page) => viewportCenter >= page.offsetTop && viewportCenter <= page.offsetTop + page.offsetHeight,
  );

  if (containingPage) {
    return containingPage.pageNumber;
  }

  return pages.reduce((nearest, page) => {
    const pageCenter = page.offsetTop + page.offsetHeight / 2;
    const nearestCenter = nearest.offsetTop + nearest.offsetHeight / 2;
    return Math.abs(pageCenter - viewportCenter) < Math.abs(nearestCenter - viewportCenter) ? page : nearest;
  }, pages[0]).pageNumber;
}

export interface RenderWindow {
  start: number;
  end: number;
}

export function computeRenderWindow(
  pages: PageScrollMetric[],
  scrollTop: number,
  viewportHeight: number,
  buffer: number,
): RenderWindow {
  if (pages.length === 0) {
    return { start: 1, end: 1 };
  }
  const top = scrollTop - buffer;
  const bottom = scrollTop + viewportHeight + buffer;
  let start = Infinity;
  let end = -Infinity;
  for (const page of pages) {
    if (page.offsetTop + page.offsetHeight >= top && page.offsetTop <= bottom) {
      start = Math.min(start, page.pageNumber);
      end = Math.max(end, page.pageNumber);
    }
  }
  return end < start ? { start: 1, end: 1 } : { start, end };
}

export function previewPageMetrics(pages: { page_no: number; height_points: number }[], zoom: number, gap: number): PageScrollMetric[] {
  let top = 0;
  return pages.map(page => { const metric = { pageNumber: page.page_no, offsetTop: top, offsetHeight: page.height_points * zoom }; top += metric.offsetHeight + gap; return metric; });
}
export function previewAnchorTop(pages: PageScrollMetric[], page: number, fraction: number): number | null {
  const metric = pages.find(p => p.pageNumber === page);
  return metric ? metric.offsetTop + metric.offsetHeight * Math.max(0, Math.min(1, fraction)) : null;
}
