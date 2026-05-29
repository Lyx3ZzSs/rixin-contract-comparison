import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.mjs?url";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist/types/src/pdf";

import { bboxToViewportRect } from "../lib/pdfCoordinates";
import type { ViewportRect } from "../lib/pdfCoordinates";
import type { DiffItem, EvidenceBox } from "../types";
import { getCurrentPageFromScroll } from "./pdfPageScroll";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

interface PageHighlight {
  diffId: string;
  type: "ADD" | "DELETE" | "MODIFY";
  evidence: EvidenceBox;
  fallback: boolean;
}

export interface PdfDocumentViewerHandle {
  scrollToDiff: (diff: DiffItem) => void;
  syncScrollFrom: (ratio: number) => void;
}

interface PdfDocumentViewerProps {
  side: "original" | "compare";
  src: string;
  title: string;
  diffs: DiffItem[];
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
      title,
      diffs,
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
    const [currentPage, setCurrentPage] = useState(1);
    const scrollRef = useRef<HTMLDivElement | null>(null);
    const pageRefs = useRef(new Map<number, HTMLDivElement>());
    const isSyncingRef = useRef(false);

    useEffect(() => {
      if (!src || hidden) {
        setPdf(null);
        setLoadState(src ? "idle" : "error");
        setCurrentPage(1);
        return;
      }

      let isMounted = true;
      const loadingTask = pdfjsLib.getDocument(src);
      setLoadState("loading");
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
        .catch(() => {
          if (isMounted) {
            setPdf(null);
            setLoadState("error");
          }
        });

      return () => {
        isMounted = false;
        loadingTask.destroy();
      };
    }, [hidden, src]);

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
    }, []);

    useEffect(() => {
      const frameId = window.requestAnimationFrame(updateCurrentPageFromScroll);
      return () => window.cancelAnimationFrame(frameId);
    }, [pdf, updateCurrentPageFromScroll, zoom]);

    const scrollToDiff = useCallback(
      (diff: DiffItem) => {
        const target = getEvidence(diff, side)[0];
        const scrollNode = scrollRef.current;
        if (!target || !scrollNode) {
          return;
        }
        const pageNode = pageRefs.current.get(target.page_no);
        if (!pageNode) {
          return;
        }
        const nextTop = pageNode.offsetTop + target.bbox.y0 * zoom - 96;
        scrollNode.scrollTo({ top: Math.max(0, nextTop), behavior: "smooth" });
      },
      [side, zoom],
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
          {loadState === "error" && <div className="empty-pane">PDF 载入失败</div>}
          {pdf &&
            Array.from({ length: pdf.numPages }, (_, index) => (
              <PdfPageCanvas
                key={`${src}-${index + 1}-${zoom}`}
                ref={(node) => {
                  if (node) {
                    pageRefs.current.set(index + 1, node);
                  } else {
                    pageRefs.current.delete(index + 1);
                  }
                }}
                pdf={pdf}
                pageNumber={index + 1}
                zoom={zoom}
                highlights={getPageHighlights(diffs, side, index + 1)}
                activeDiffId={activeDiffId}
                onActivateDiff={onActivateDiff}
              />
            ))}
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
    highlights: PageHighlight[];
    activeDiffId: string;
    onActivateDiff: (diffId: string) => void;
  }
>(function PdfPageCanvas({ pdf, pageNumber, zoom, highlights, activeDiffId, onActivateDiff }, ref) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [pageSize, setPageSize] = useState({ width: 0, height: 0 });

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

export function getPageHighlights(
  diffs: DiffItem[],
  side: "original" | "compare",
  pageNumber: number,
): PageHighlight[] {
  const highlights: PageHighlight[] = [];
  for (const diff of diffs) {
    const evidences = getEvidence(diff, side)
      .filter((evidence) => evidence.page_no === pageNumber)
      .sort((left, right) => left.bbox.y0 - right.bbox.y0 || left.bbox.x0 - right.bbox.x0);

    for (const evidence of evidences) {
      const type = evidence.highlight_type ?? diff.diff_type;
      const fallback = evidence.method === "block_fallback";
      const previous = highlights[highlights.length - 1];
      if (previous && canMergeHighlight(previous, diff.diff_id, type, evidence, fallback)) {
        previous.evidence = mergeEvidence(previous.evidence, evidence);
        continue;
      }
      highlights.push({ diffId: diff.diff_id, type, evidence, fallback });
    }
  }
  return highlights;
}

function canMergeHighlight(
  current: PageHighlight,
  diffId: string,
  type: PageHighlight["type"],
  next: EvidenceBox,
  fallback: boolean,
): boolean {
  if (current.diffId !== diffId || current.type !== type || current.fallback || fallback) {
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
        const markKind = highlight.fallback
          ? "fallback"
          : (highlight.evidence.method || "").startsWith("table")
            ? "table"
            : "text";
        const isActive = activeDiffId === highlight.diffId;
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
            <rect
              x={rect.x}
              y={rect.y}
              width={rect.width}
              height={rect.height}
              rx={2.5}
              ry={2.5}
            />
          </g>
        );
      })}
    </svg>
  );
}
