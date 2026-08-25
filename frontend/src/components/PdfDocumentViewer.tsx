import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import * as pdfjsLib from "pdfjs-dist/legacy/build/pdf.mjs";
import workerUrl from "pdfjs-dist/legacy/build/pdf.worker.mjs?url";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist/types/src/pdf";

import { bboxToViewportRect } from "../lib/pdfCoordinates";
import type { ViewportRect } from "../lib/pdfCoordinates";
import { formatPdfLoadError } from "../lib/pdfLoadError";
import type { DiffItem, EvidenceBox } from "../types";
import { computeRenderWindow, getCurrentPageFromScroll } from "./pdfPageScroll";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

const DEFAULT_PAGE_HEIGHT = 842; // A4 pt, matches pdfjs viewport height at zoom 1
const RENDER_BUFFER = 800; // pre-render runway above/below the viewport (~1 page)

interface RenderRange {
  start: number;
  end: number;
}

interface PageHighlight {
  diffId: string;
  type: "ADD" | "DELETE" | "MODIFY";
  evidence: EvidenceBox;
  fallback: boolean;
  markKind: "fallback" | "seal" | "signing-region" | "table" | "text";
  recognition?: boolean;
}

export interface PdfDocumentViewerHandle {
  scrollToDiff: (diff: DiffItem) => number | null;
  syncScrollFrom: (ratio: number) => void;
}

interface PdfDocumentViewerProps {
  side: "original" | "compare";
  src: string;
  accessToken: string;
  title: string;
  diffs: DiffItem[];
  recognitionOutlines?: EvidenceBox[];
  zoom: number;
  activeDiffId: string;
  hidden?: boolean;
  syncEnabled: boolean;
  onScrollRatio: (ratio: number, source: "original" | "compare") => void;
  onActivateDiff: (diffId: string) => void;
}

export const PdfDocumentViewer = forwardRef<PdfDocumentViewerHandle, PdfDocumentViewerProps>(
  function PdfDocumentViewer(
    {
      side,
      src,
      accessToken,
      title,
      diffs,
      recognitionOutlines = [],
      zoom,
      hidden = false,
      syncEnabled,
      onScrollRatio,
      activeDiffId,
      onActivateDiff,
    },
    ref,
  ) {
    const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
    const [loadState, setLoadState] = useState<"idle" | "loading" | "ready" | "error">("idle");
    const [loadError, setLoadError] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const [pageHeights, setPageHeights] = useState<Record<number, number>>({});
    const [renderRange, setRenderRange] = useState<RenderRange>({ start: 1, end: 3 });
    const scrollRef = useRef<HTMLDivElement | null>(null);
    const pageRefs = useRef(new Map<number, HTMLDivElement>());
    const isSyncingRef = useRef(false);
    // OIDC token rotation must not destroy an in-flight PDF.js loading task.
    const accessTokenRef = useRef(accessToken);
    accessTokenRef.current = accessToken;
    const hasAccessToken = Boolean(accessToken);

    useEffect(() => {
      if (!src || hidden) {
        setPdf(null);
        setLoadState(src ? "idle" : "error");
        setLoadError(src ? "" : "PDF 文件地址不可用。");
        setCurrentPage(1);
        return;
      }

      let isMounted = true;
      const loadingTask = pdfjsLib.getDocument({
        url: src,
        ...(hasAccessToken ? { httpHeaders: { Authorization: `Bearer ${accessTokenRef.current}` } } : {}),
      });
      setLoadState("loading");
      setLoadError("");
      setCurrentPage(1);

      loadingTask.promise
        .then((document) => {
          if (!isMounted) {
            document.destroy();
            return;
          }
          setPdf(document);
          setLoadState("ready");
          setCurrentPage(1);
        })
        .catch((error) => {
          if (isMounted) {
            console.error("PDF preview loading failed", error);
            setPdf(null);
            setLoadError(formatPdfLoadError(error));
            setLoadState("error");
          }
        });

      return () => {
        isMounted = false;
        loadingTask.destroy();
      };
    }, [hasAccessToken, hidden, src]);

    const updateCurrentPageFromScroll = useCallback(() => {
      const scrollNode = scrollRef.current;
      if (!scrollNode) {
        return;
      }
      const pages = Array.from(pageRefs.current.entries())
        .map(([pageNumber, node]) => ({
          pageNumber,
          offsetTop: node.offsetTop,
          offsetHeight: node.offsetHeight,
        }))
        .sort((left, right) => left.pageNumber - right.pageNumber);
      setCurrentPage(getCurrentPageFromScroll(scrollNode.scrollTop, scrollNode.clientHeight, pages));
      setRenderRange(computeRenderWindow(pages, scrollNode.scrollTop, scrollNode.clientHeight, RENDER_BUFFER));
    }, []);

    const reservedHeight = useMemo(() => {
      const heights = Object.values(pageHeights);
      return heights.length > 0 ? heights[heights.length - 1] : DEFAULT_PAGE_HEIGHT * zoom;
    }, [pageHeights, zoom]);

    const handlePageSized = useCallback((pageNumber: number, height: number) => {
      setPageHeights((prev) => (prev[pageNumber] === height ? prev : { ...prev, [pageNumber]: height }));
    }, []);

    useEffect(() => {
      const frameId = window.requestAnimationFrame(updateCurrentPageFromScroll);
      return () => window.cancelAnimationFrame(frameId);
    }, [pageHeights, pdf, updateCurrentPageFromScroll, zoom]);

    // A new document or a zoom change invalidates previously measured page heights.
    useEffect(() => {
      setPageHeights({});
      setRenderRange({ start: 1, end: 3 });
    }, [pdf, zoom]);

    const scrollToDiff = useCallback(
      (diff: DiffItem) => {
        const target = getScrollTargetEvidence(diff, side);
        const scrollNode = scrollRef.current;
        if (!target || !scrollNode) {
          return null;
        }
        const pageNode = pageRefs.current.get(target.page_no);
        if (!pageNode) {
          return null;
        }
        const nextTop = pageNode.offsetTop + target.bbox.y0 * zoom - 96;
        const maxScroll = Math.max(0, scrollNode.scrollHeight - scrollNode.clientHeight);
        const targetTop = Math.min(maxScroll, Math.max(0, nextTop));
        isSyncingRef.current = true;
        scrollNode.scrollTo({ top: targetTop, behavior: "auto" });
        updateCurrentPageFromScroll();
        window.setTimeout(() => {
          isSyncingRef.current = false;
        }, 80);
        return maxScroll > 0 ? targetTop / maxScroll : 0;
      },
      [side, updateCurrentPageFromScroll, zoom],
    );

    useImperativeHandle(
      ref,
      () => ({
        scrollToDiff,
        syncScrollFrom(ratio: number) {
          const scrollNode = scrollRef.current;
          if (!scrollNode) {
            return;
          }
          const maxScroll = scrollNode.scrollHeight - scrollNode.clientHeight;
          isSyncingRef.current = true;
          scrollNode.scrollTop = Math.max(0, maxScroll * ratio);
          updateCurrentPageFromScroll();
          window.setTimeout(() => {
            isSyncingRef.current = false;
          }, 80);
        },
      }),
      [scrollToDiff, updateCurrentPageFromScroll],
    );

    function handleScroll(event: React.UIEvent<HTMLDivElement>) {
      updateCurrentPageFromScroll();
      if (!syncEnabled || isSyncingRef.current) {
        return;
      }
      const target = event.currentTarget;
      const maxScroll = target.scrollHeight - target.clientHeight;
      onScrollRatio(maxScroll > 0 ? target.scrollTop / maxScroll : 0, side);
    }

    if (hidden) {
      return (
        <article className={`pdf-pane ${side}`} aria-label={`${title}已隐藏`}>
          <div className="pdf-scroll-shell">
            <div className="empty-pane">原版已隐藏</div>
          </div>
        </article>
      );
    }

    return (
      <article className={`pdf-pane ${side}`} aria-label={`${title}PDF 在线预览`}>
        <div ref={scrollRef} className="pdf-scroll-shell" onScroll={handleScroll}>
          {loadState === "loading" && <div className="empty-pane">正在载入 PDF...</div>}
          {loadState === "error" && <div className="empty-pane">{loadError || "PDF 载入失败"}</div>}
          {pdf &&
            Array.from({ length: pdf.numPages }, (_, index) => {
              const pageNumber = index + 1;
              const pageRef = (node: HTMLDivElement | null) => {
                if (node) {
                  pageRefs.current.set(pageNumber, node);
                } else {
                  pageRefs.current.delete(pageNumber);
                }
              };
              if (pageNumber < renderRange.start || pageNumber > renderRange.end) {
                return (
                  <div
                    key={`ph-${src}-${pageNumber}`}
                    ref={pageRef}
                    className="pdf-page-frame pdf-page-placeholder"
                    data-page-number={pageNumber}
                    aria-hidden="true"
                    style={{ height: pageHeights[pageNumber] ?? reservedHeight }}
                  />
                );
              }
              return (
                <PdfPageCanvas
                  key={`${src}-${pageNumber}-${zoom}`}
                  ref={pageRef}
                  pdf={pdf}
                  pageNumber={pageNumber}
                  zoom={zoom}
                  initialHeight={reservedHeight}
                  onSized={handlePageSized}
                  highlights={getPageHighlights(diffs, side, pageNumber, recognitionOutlines)}
                  activeDiffId={activeDiffId}
                  onActivateDiff={onActivateDiff}
                />
              );
            })}
        </div>
        {pdf && loadState === "ready" && (
          <div className="pdf-page-indicator" aria-label="PDF 当前页码">
            {currentPage}/{pdf.numPages}
          </div>
        )}
      </article>
    );
  },
);

const PdfPageCanvas = forwardRef<
  HTMLDivElement,
  {
    pdf: PDFDocumentProxy;
    pageNumber: number;
    zoom: number;
    initialHeight: number;
    onSized: (pageNumber: number, height: number) => void;
    highlights: PageHighlight[];
    activeDiffId: string;
    onActivateDiff: (diffId: string) => void;
  }
>(function PdfPageCanvas({ pdf, pageNumber, zoom, initialHeight, onSized, highlights, activeDiffId, onActivateDiff }, ref) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [pageSize, setPageSize] = useState({ width: 0, height: initialHeight });

  useEffect(() => {
    let isMounted = true;
    let renderTask: RenderTask | null = null;

    pdf.getPage(pageNumber).then((page) => {
      if (!isMounted) {
        return;
      }
      const viewport = page.getViewport({ scale: zoom });
      const canvas = canvasRef.current;
      if (!canvas) {
        return;
      }
      const context = canvas.getContext("2d");
      if (!context) {
        return;
      }
      const outputScale = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      setPageSize({ width: viewport.width, height: viewport.height });
      onSized(pageNumber, viewport.height);

      const task = page.render({
        canvas,
        canvasContext: context,
        viewport,
        transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
      });
      renderTask = task;
      task.promise.catch(() => {
        // Rendering can be cancelled during zoom changes; no user-facing error is needed.
      });
    });

    return () => {
      isMounted = false;
      renderTask?.cancel();
    };
  }, [pageNumber, pdf, zoom]);

  return (
    <div
      ref={ref}
      className="pdf-page-frame"
      style={{ width: pageSize.width || undefined, height: pageSize.height || undefined }}
      data-page-number={pageNumber}
    >
      <canvas ref={canvasRef} aria-label={`第 ${pageNumber} 页`} />
      <PdfHighlightLayer
        activeDiffId={activeDiffId}
        highlights={highlights}
        pageSize={pageSize}
        zoom={zoom}
        onActivateDiff={onActivateDiff}
      />
    </div>
  );
});

function getEvidence(diff: DiffItem, side: "original" | "compare"): EvidenceBox[] {
  return side === "original" ? (diff.original_evidence ?? []) : (diff.compare_evidence ?? []);
}

export function getScrollTargetEvidence(
  diff: DiffItem,
  side: "original" | "compare",
): EvidenceBox | undefined {
  const evidences = getEvidence(diff, side);
  return evidences.find((evidence) => evidence.method === "signing_region_visual") ?? evidences[0];
}

export function getPageHighlights(
  diffs: DiffItem[],
  side: "original" | "compare",
  pageNumber: number,
  recognitionOutlines: EvidenceBox[] = [],
): PageHighlight[] {
  const highlights: PageHighlight[] = [];
  for (const diff of diffs) {
    const pageEvidences = getEvidence(diff, side).filter((evidence) => evidence.page_no === pageNumber);
    const hasTypedSigningEvidence = pageEvidences.some(
      (evidence) => evidence.method.startsWith("signing_region") && Boolean(evidence.highlight_type),
    );
    const evidences = pageEvidences
      .filter(
        (evidence) => {
          const isUntypedSigningContext =
            hasTypedSigningEvidence
            && evidence.method.startsWith("signing_region")
            && !evidence.highlight_type;
          return !isUntypedSigningContext;
        },
      )
      .sort((left, right) => left.bbox.y0 - right.bbox.y0 || left.bbox.x0 - right.bbox.x0);

    for (const evidence of evidences) {
      const type = evidence.highlight_type ?? diff.diff_type;
      const fallback = evidence.method === "block_fallback";
      const markKind = highlightMarkKind(evidence, fallback);
      const previous = highlights[highlights.length - 1];
      if (previous && canMergeHighlight(previous, diff.diff_id, type, evidence, fallback, markKind)) {
        previous.evidence = mergeEvidence(previous.evidence, evidence);
        continue;
      }
      highlights.push({ diffId: diff.diff_id, type, evidence, fallback, markKind });
    }
  }
  highlights.sort((left, right) => highlightArea(right) - highlightArea(left));
  const regionHighlights = recognitionOutlines
    .filter((evidence) => evidence.page_no === pageNumber)
    .filter(
      (evidence) => !highlights.some(
        (highlight) => highlight.markKind === "signing-region" && sameBBox(highlight.evidence, evidence),
      ),
    )
    .map((evidence, index) => ({
      diffId: `signing-region-outline-${pageNumber}-${index}`,
      type: "MODIFY" as const,
      evidence,
      fallback: false,
      markKind: "signing-region" as const,
      recognition: true,
    }));
  return [...regionHighlights, ...highlights];
}

function sameBBox(left: EvidenceBox, right: EvidenceBox): boolean {
  return ["x0", "y0", "x1", "y1"].every(
    (axis) => Math.abs(left.bbox[axis as keyof EvidenceBox["bbox"]] - right.bbox[axis as keyof EvidenceBox["bbox"]]) < 0.5,
  );
}

function highlightArea(highlight: PageHighlight): number {
  const { x0, y0, x1, y1 } = highlight.evidence.bbox;
  return Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
}

function highlightMarkKind(evidence: EvidenceBox, fallback: boolean): PageHighlight["markKind"] {
  const method = evidence.method || "";
  if (fallback) {
    return "fallback";
  }
  if (method === "seal_region") {
    return "seal";
  }
  if (method === "signing_region_element") {
    return "text";
  }
  if (method.startsWith("signing_region")) {
    return "signing-region";
  }
  if (method.startsWith("table")) {
    return "table";
  }
  return "text";
}

function canMergeHighlight(
  current: PageHighlight,
  diffId: string,
  type: PageHighlight["type"],
  next: EvidenceBox,
  fallback: boolean,
  markKind: PageHighlight["markKind"],
): boolean {
  if (current.diffId !== diffId || current.type !== type || current.fallback || fallback || current.markKind !== markKind) {
    return false;
  }
  const currentBox = current.evidence.bbox;
  const nextBox = next.bbox;
  const currentHeight = Math.max(1, currentBox.y1 - currentBox.y0);
  const nextHeight = Math.max(1, nextBox.y1 - nextBox.y0);
  const centerDelta = Math.abs((currentBox.y0 + currentBox.y1) / 2 - (nextBox.y0 + nextBox.y1) / 2);
  const horizontalGap = nextBox.x0 - currentBox.x1;
  return centerDelta <= Math.max(currentHeight, nextHeight) * 0.5 && horizontalGap >= 0 && horizontalGap <= 12;
}

function mergeEvidence(left: EvidenceBox, right: EvidenceBox): EvidenceBox {
  return {
    ...left,
    bbox: {
      x0: Math.min(left.bbox.x0, right.bbox.x0),
      y0: Math.min(left.bbox.y0, right.bbox.y0),
      x1: Math.max(left.bbox.x1, right.bbox.x1),
      y1: Math.max(left.bbox.y1, right.bbox.y1),
    },
    text: [left.text, right.text].filter(Boolean).join(" "),
  };
}

export function highlightRect(highlight: PageHighlight, zoom: number): ViewportRect {
  return bboxToViewportRect(highlight.evidence.bbox, zoom);
}

export function PdfHighlightLayer({
  activeDiffId,
  highlights,
  pageSize,
  zoom,
  onActivateDiff,
}: {
  activeDiffId: string;
  highlights: PageHighlight[];
  pageSize: { width: number; height: number };
  zoom: number;
  onActivateDiff: (diffId: string) => void;
}) {
  if (pageSize.width <= 0 || pageSize.height <= 0) {
    return null;
  }

  return (
    <svg
      className="pdf-highlight-layer"
      aria-hidden={false}
      width={pageSize.width}
      height={pageSize.height}
      viewBox={`0 0 ${pageSize.width} ${pageSize.height}`}
    >
      {highlights.map((highlight, index) => {
        const rect = highlightRect(highlight, zoom);
        const markKind = highlight.markKind;
        const isActive = activeDiffId === highlight.diffId;
        const rectangle = (
          <rect
            x={rect.x}
            y={rect.y}
            width={rect.width}
            height={rect.height}
            rx={2.5}
            ry={2.5}
          />
        );
        if (highlight.recognition) {
          return (
            <g
              key={`${highlight.diffId}-${index}`}
              className="pdf-highlight-mark signing-region recognition"
              data-recognition-outline="true"
            >
              {rectangle}
            </g>
          );
        }
        return (
          <g
            key={`${highlight.diffId}-${index}`}
            role="button"
            tabIndex={0}
            className={[
              "pdf-highlight-mark",
              highlight.type.toLowerCase(),
              markKind,
              isActive ? "active" : "muted",
            ].join(" ")}
            aria-label={`定位差异 ${highlight.diffId}`}
            onClick={() => onActivateDiff(highlight.diffId)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onActivateDiff(highlight.diffId);
              }
            }}
          >
            {rectangle}
          </g>
        );
      })}
    </svg>
  );
}
