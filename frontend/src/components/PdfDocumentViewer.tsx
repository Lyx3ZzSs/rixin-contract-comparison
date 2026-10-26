import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.mjs?url';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/pdf';
import { bboxToViewportRect } from '../lib/pdfCoordinates';
import type { ViewportRect } from '../lib/pdfCoordinates';
import { formatPdfLoadError } from '../lib/pdfLoadError';
import type { DiffItem, EvidenceBox } from '../types';
import { computeRenderWindow, getCurrentPageFromScroll } from './pdfPageScroll';
import { canvasKey } from '../lib/pdfCanvasCache';
import { onPreviewIdle, PdfPreviewRuntime, previewTransport } from '../lib/pdfPreviewRuntime';
import { previewPolicy, type PreviewDocument, type PreviewPage } from '../lib/pdfPreviewTypes';
pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;
interface PageHighlight {
  diffId: string; type: 'ADD' | 'DELETE' | 'MODIFY'; evidence: EvidenceBox; fallback: boolean;
  markKind: 'fallback' | 'seal' | 'signing-region' | 'observation' | 'table' | 'text'; recognition?: boolean;
}
export interface PdfDocumentViewerHandle {
  scrollToDiff: (diff: DiffItem) => number | null;
  syncScrollFrom: (ratio: number) => void;
  scrollToPage: (page: number, fraction?: number) => void;
}
interface PdfDocumentViewerProps {
  side: 'original' | 'compare'; src: string; accessToken: string; title: string;
  diffs: DiffItem[]; recognitionOutlines?: EvidenceBox[]; zoom: number; activeDiffId: string;
  hidden?: boolean; syncEnabled: boolean; runtime: PdfPreviewRuntime;
  metadata?: PreviewDocument | null;
  onScrollRatio: (ratio: number, source: 'original' | 'compare') => void;
  onActivateDiff: (diffId: string) => void;
  onPageScroll?: (page: number, fraction: number, side: 'original' | 'compare') => void;
  onNavigationReady?: (page: number, side: 'original' | 'compare') => void;
}
export const PdfDocumentViewer = forwardRef<PdfDocumentViewerHandle, PdfDocumentViewerProps>(function PdfDocumentViewer(
  { side, src, accessToken, title, diffs, recognitionOutlines = [], zoom, hidden = false, syncEnabled, onScrollRatio, activeDiffId, onActivateDiff, onPageScroll, onNavigationReady, runtime, metadata }, ref,
) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [documentVersion, setDocumentVersion] = useState(0);
  const [loadedSource, setLoadedSource] = useState({ src: '', version: -1 });
  const knownSource = useRef<{ src: string; revision?: string }>({ src });
  useEffect(() => {
    const revision = metadata?.source_revision;
    const previous = knownSource.current;
    if (previous.src === src && previous.revision && revision && previous.revision !== revision) setDocumentVersion(n => n + 1);
    if (previous.src !== src || revision) knownSource.current = { src, revision };
  }, [src, metadata?.source_revision]);
  const revisionChanged = knownSource.current.src === src && Boolean(knownSource.current.revision && metadata?.source_revision && knownSource.current.revision !== metadata.source_revision);
  const documentIsCurrent = loadedSource.src === src && loadedSource.version === documentVersion && !revisionChanged;
  const [loadState, setLoadState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [loadError, setLoadError] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [sizes, setSizes] = useState<Record<number, PreviewPage>>({});
  const [visible, setVisible] = useState({ start: 1, end: 1 });
  const [targetPage, setTargetPage] = useState<number | null>(null);
  const [dpr, setDpr] = useState(() => window.devicePixelRatio || 1);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef(new Map<number, HTMLDivElement>());
  const isSyncing = useRef(false);
  const navigationId = useRef(0);
  const pending = useRef<{ id: number; page: number; fraction: number; y?: number; appliedGeneration?: number } | null>(null);
  const unlockTimer = useRef<number | undefined>(undefined);
  const anchor = useRef({ page: 1, fraction: 0 });
  const previousZoom = useRef(zoom);
  const token = useRef(accessToken); token.current = accessToken;
  const hasToken = Boolean(accessToken);
  const geometry = useMemo<Record<number, PreviewPage>>(() => ({ ...Object.fromEntries((metadata?.pages ?? []).map(p => [p.page_no, p])), ...sizes }), [metadata, sizes]);
  const geometryRef = useRef(geometry); geometryRef.current = geometry;
  const sourceRevision = metadata?.source_revision ?? src;
  useEffect(() => () => runtime.cache.invalidateDocument(sourceRevision), [runtime, sourceRevision]);
  const generation = useMemo(() => runtime.nextGeneration(), [runtime, pdf, zoom, dpr]);
  const currentGeneration = useRef(generation); currentGeneration.current = generation;
  useEffect(() => () => runtime.scheduler.cancelGeneration(generation), [runtime, generation]);
  useEffect(() => {
    let resolution: MediaQueryList | undefined;
    const resize = () => {
      setDpr(window.devicePixelRatio || 1);
      resolution?.removeEventListener('change', resize);
      resolution = typeof window.matchMedia === 'function' ? window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`) : undefined;
      resolution?.addEventListener('change', resize);
    };
    resize(); window.addEventListener('resize', resize);
    return () => { window.removeEventListener('resize', resize); resolution?.removeEventListener('change', resize); };
  }, []);
  useEffect(() => {
    setPdf(null); setSizes({}); setVisible({ start: 1, end: 1 }); setCurrentPage(1);
    anchor.current = { page: 1, fraction: 0 }; pending.current = null; navigationId.current++;
    if (!src || hidden) { setLoadState(src ? 'idle' : 'error'); setLoadError(src ? '' : 'PDF 文件地址不可用。'); return; }
    let mounted = true;
    const loading = pdfjsLib.getDocument({ url: src,
      cMapUrl: `${import.meta.env.BASE_URL}pdfjs/cmaps/`, cMapPacked: true,
      ...previewTransport(), ...(hasToken ? { httpHeaders: { Authorization: `Bearer ${token.current}` } } : {}) });
    setLoadState('loading'); setLoadError('');
    loading.promise.then(doc => { if (mounted) { setLoadedSource({ src, version: documentVersion }); setPdf(doc); setLoadState('ready'); } }).catch(error => {
      if (mounted) { console.error('PDF preview loading failed', error); setLoadError(formatPdfLoadError(error)); setLoadState('error'); }
    });
    return () => { mounted = false; navigationId.current++; window.clearTimeout(unlockTimer.current); void loading.destroy(); };
  }, [src, hidden, hasToken, documentVersion]);
  const onSized = useCallback((page: PreviewPage) => {
    const old = geometryRef.current[page.page_no];
    if (old?.width_points === page.width_points && old.height_points === page.height_points && old.rotation === page.rotation) return;
    setSizes(prev => ({ ...prev, [page.page_no]: page }));
  }, []);
  // Fallback geometry uses PDF.js itself. Only lightweight page dictionaries, never all canvases.
  useEffect(() => {
    if (!pdf || metadata !== null) return;
    let cancelled = false;
    void (async () => {
      for (let n = 1; n <= pdf.numPages && !cancelled; n++) {
        if (geometryRef.current[n]) continue;
        const page = await pdf.getPage(n); if (cancelled) return;
        const viewport = page.getViewport({ scale: 1 });
        onSized({ page_no: n, width_points: viewport.width, height_points: viewport.height, rotation: page.rotate as PreviewPage['rotation'] });
      }
    })().catch(() => { /* Per-page loading reports actionable errors. */ });
    return () => { cancelled = true; };
  }, [pdf, metadata, onSized]);
  const metrics = useCallback(() => [...pageRefs.current].map(([pageNumber, node]) => ({ pageNumber, offsetTop: node.offsetTop, offsetHeight: node.offsetHeight })), []);
  const updateWindow = useCallback(() => {
    const node = scrollRef.current; if (!node) return;
    const pages = metrics();
    setCurrentPage(getCurrentPageFromScroll(node.scrollTop, node.clientHeight, pages));
    const next = computeRenderWindow(pages, node.scrollTop, node.clientHeight, 0);
    setVisible(old => old.start === next.start && old.end === next.end ? old : next);
    const page = pages.filter(p => p.offsetTop <= node.scrollTop + 1).at(-1);
    if (page) anchor.current = { page: page.pageNumber, fraction: (node.scrollTop - page.offsetTop) / Math.max(1, page.offsetHeight) };
  }, [metrics]);
  const releaseLock = useCallback(() => {
    window.clearTimeout(unlockTimer.current);
    // Let the programmatic scroll event drain before permitting reciprocal synchronization.
    requestAnimationFrame(() => requestAnimationFrame(() => { if (!pending.current) isSyncing.current = false; }));
  }, []);
  const onReady = useCallback((page: number, renderedGeneration: number) => {
    const nav = pending.current;
    if (!nav || nav.page !== page || nav.appliedGeneration !== renderedGeneration || currentGeneration.current !== renderedGeneration) return;
    requestAnimationFrame(() => {
      if (pending.current?.id !== nav.id || nav.appliedGeneration !== renderedGeneration || currentGeneration.current !== renderedGeneration) return;
      pending.current = null; releaseLock(); onNavigationReady?.(page, side);
    });
  }, [onNavigationReady, releaseLock, side]);
  const applyNavigation = useCallback(() => {
    const nav = pending.current; const scroller = scrollRef.current;
    if (!nav || !scroller) return;
    // Every preceding page needs a known height before a distant target can be placed accurately.
    if (Array.from({ length: nav.page }, (_, i) => i + 1).some(n => !geometryRef.current[n])) return;
    const frame = pageRefs.current.get(nav.page); if (!frame) return;
    scroller.scrollTop = Math.max(0, frame.offsetTop + (nav.y === undefined ? nav.fraction * frame.offsetHeight : nav.y * zoom - 96));
    nav.appliedGeneration = generation;
    updateWindow();
    if (frame.querySelector(`canvas[data-render-state="ready"][data-render-generation="${generation}"]`)) onReady(nav.page, generation);
  }, [zoom, generation, updateWindow, onReady]);
  const navigate = useCallback((page: number, fraction = 0, y?: number) => {
    if (!pdf || page < 1 || page > pdf.numPages) return;
    const id = ++navigationId.current;
    pending.current = { id, page, fraction: Math.max(0, Math.min(1, fraction)), y };
    setTargetPage(page); isSyncing.current = true;
    window.clearTimeout(unlockTimer.current);
    unlockTimer.current = window.setTimeout(() => { if (pending.current?.id === id) { pending.current = null; isSyncing.current = false; } }, 10000);
    applyNavigation();
    void (async () => {
      // Without metadata prioritize target geometry, then fill required preceding heights.
      for (const n of [page, ...Array.from({ length: page - 1 }, (_, i) => i + 1)]) {
        if (id !== navigationId.current) return;
        if (geometryRef.current[n]) continue;
        const p = await pdf.getPage(n); if (id !== navigationId.current) return;
        const v = p.getViewport({ scale: 1 });
        onSized({ page_no: n, width_points: v.width, height_points: v.height, rotation: p.rotate as PreviewPage['rotation'] });
      }
    })().catch(() => { if (id === navigationId.current) { pending.current = null; releaseLock(); } });
  }, [pdf, applyNavigation, onSized, releaseLock]);
  useLayoutEffect(() => {
    if (previousZoom.current !== zoom) {
      previousZoom.current = zoom;
      const saved = { ...anchor.current };
      if (pending.current) { applyNavigation(); } else navigate(saved.page, saved.fraction);
    } else if (pending.current) applyNavigation();
    else updateWindow();
  }, [zoom, geometry, pdf, navigate, applyNavigation, updateWindow]);
  useImperativeHandle(ref, () => ({
    scrollToDiff(diff) {
      const evidence = getScrollTargetEvidence(diff, side); if (!evidence || !pdf) return null;
      navigate(evidence.page_no, 0, evidence.bbox.y0);
      const s = scrollRef.current; return s ? s.scrollTop / Math.max(1, s.scrollHeight - s.clientHeight) : 0;
    },
    scrollToPage: navigate,
    syncScrollFrom(ratio) {
      const s = scrollRef.current; if (!s) return;
      const top = Math.max(0, (s.scrollHeight - s.clientHeight) * ratio);
      const page = metrics().filter(p => p.offsetTop <= top).at(-1);
      if (page) navigate(page.pageNumber, (top - page.offsetTop) / Math.max(1, page.offsetHeight));
    },
  }), [pdf, navigate, side, metrics]);
  function interruptNavigation() { navigationId.current++; pending.current = null; isSyncing.current = false; window.clearTimeout(unlockTimer.current); setTargetPage(null); }
  function handleScroll() {
    updateWindow();
    if (!syncEnabled || isSyncing.current) return;
    const s = scrollRef.current; if (!s) return;
    if (onPageScroll) onPageScroll(anchor.current.page, anchor.current.fraction, side);
    else onScrollRatio(s.scrollTop / Math.max(1, s.scrollHeight - s.clientHeight), side);
  }
  if (hidden) return <article className={`pdf-pane ${side}`} aria-label={`${title}已隐藏`}><div className="empty-pane">原版已隐藏</div></article>;
  return <article className={`pdf-pane ${side}`} aria-label={`${title}PDF 在线预览`}>
    <div ref={scrollRef} className="pdf-scroll-shell" onScroll={handleScroll} onWheel={interruptNavigation} onPointerDown={interruptNavigation} style={{ overflowAnchor: 'none', position: 'relative' }}>
      {loadState === 'loading' && <div className="empty-pane">正在载入 PDF...</div>}
      {loadState === 'error' && <div className="empty-pane">{loadError || 'PDF 载入失败'}</div>}
      {pdf && documentIsCurrent && Array.from({ length: pdf.numPages }, (_, i) => {
        const n = i + 1; const page = geometry[n];
        const mount = n === targetPage || n >= visible.start - previewPolicy.neighborPages && n <= visible.end + previewPolicy.neighborPages;
        return <div key={`${src}-${n}`} ref={node => { if (node) pageRefs.current.set(n, node); else pageRefs.current.delete(n); }} className={`pdf-page-frame${mount ? '' : ' pdf-page-placeholder'}`} data-page-number={n} style={{ width: (page?.width_points ?? 595) * zoom, height: (page?.height_points ?? 842) * zoom }}>
          {mount && <PdfPageCanvas pdf={pdf} pageNumber={n} zoom={zoom} dpr={dpr} runtime={runtime} generation={generation} side={side} revision={metadata?.source_revision ?? src} priority={n === targetPage ? 0 : n >= visible.start && n <= visible.end ? 1 : 2} onSized={onSized} onReady={onReady} highlights={getPageHighlights(diffs, side, n, recognitionOutlines)} activeDiffId={activeDiffId} onActivateDiff={onActivateDiff} />}
        </div>;
      })}
    </div>
    {pdf && documentIsCurrent && loadState === 'ready' && <div className="pdf-page-indicator" aria-label="PDF 当前页码">{currentPage}/{pdf.numPages}</div>}
  </article>;
});
function PdfPageCanvas({ pdf, pageNumber, zoom, dpr, runtime, generation, side, revision, priority, onSized, onReady, highlights, activeDiffId, onActivateDiff }: {
  pdf: PDFDocumentProxy; pageNumber: number; zoom: number; dpr: number; runtime: PdfPreviewRuntime; generation: number;
  side: 'original' | 'compare'; revision: string; priority: 0 | 1 | 2; onSized: (page: PreviewPage) => void; onReady: (page: number, generation: number) => void;
  highlights: PageHighlight[]; activeDiffId: string; onActivateDiff: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [pageSize, setPageSize] = useState({ width: 0, height: 0 });
  const [error, setError] = useState(''); const [retry, setRetry] = useState(0);
  const ready = useRef(onReady); ready.current = onReady;
  useEffect(() => {
    const controller = new AbortController(); let canvas: HTMLCanvasElement | null = null; let key = ''; let complete = false; let settled = false; let cancelIdle = () => {};
    const cleanupCanvas = () => { if (!canvas) return; if (complete) runtime.cache.release(key, canvas); else runtime.cache.discard(key, canvas); canvas = null; runtime.publish(); };
    setError('');
    const execute = async () => {
      const page = await pdf.getPage(pageNumber); if (controller.signal.aborted) return;
      const viewport = page.getViewport({ scale: zoom });
      onSized({ page_no: pageNumber, width_points: viewport.width / zoom, height_points: viewport.height / zoom, rotation: page.rotate as PreviewPage['rotation'] });
      setPageSize({ width: viewport.width, height: viewport.height });
      key = canvasKey(runtime.taskId, side, revision, pageNumber, zoom, dpr, page.rotate);
      canvas = runtime.cache.acquire(key);
      if (canvas) { complete = true; canvas.dataset.renderGeneration = String(generation); host.current?.append(canvas); runtime.publish(); ready.current(pageNumber, generation); return; }
      const render = async () => {
        if (controller.signal.aborted) return;
        await runtime.scheduler.enqueue({ key, generation, side, priority, signal: controller.signal, run: async signal => {
          canvas = document.createElement('canvas'); canvas.setAttribute('aria-label', `第 ${pageNumber} 页`);
          canvas.dataset.renderGeneration = String(generation);
          canvas.width = Math.floor(viewport.width * dpr); canvas.height = Math.floor(viewport.height * dpr);
          canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
          runtime.cache.track(key, canvas); host.current?.append(canvas);
          const context = canvas.getContext('2d'); if (!context) throw Error('无法创建页面画布');
          const size = canvas.width * canvas.height * 4; runtime.renderingBytes += size; runtime.renders++;
          let cancel = () => {};
          try {
            const task = page.render({ canvas, canvasContext: context, viewport, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined });
            cancel = () => task.cancel(); signal.addEventListener('abort', cancel, { once: true });
            await task.promise;
            if (!signal.aborted && !controller.signal.aborted) {
              complete = true; canvas.dataset.renderState = 'ready'; canvas.dataset.readyAt = String(performance.now()); ready.current(pageNumber, generation);
            }
          } finally {
            signal.removeEventListener('abort', cancel); runtime.renderingBytes -= size;
            runtime.publish(); window.setTimeout(() => runtime.publish(), 0);
          }
        } });
      };
      // A preempted neighbor retries only while still relevant to the current window.
      const attempt = async (): Promise<void> => {
        try { await render(); } catch (e) {
          if (e instanceof DOMException && e.name === 'AbortError' && !controller.signal.aborted) {
            cleanupCanvas(); await idle(); return attempt();
          }
          throw e;
        }
      };
      function idle() { return new Promise<void>(resolve => { const finish = () => { controller.signal.removeEventListener('abort', finish); resolve(); }; cancelIdle = onPreviewIdle(finish); controller.signal.addEventListener('abort', finish, { once: true }); }); }
      if (priority === 2) await idle();
      await attempt();
    };
    void execute().catch(e => { if (!controller.signal.aborted) { cleanupCanvas(); setError(formatPdfLoadError(e)); } }).finally(() => { settled = true; if (controller.signal.aborted) cleanupCanvas(); });
    return () => { controller.abort(); cancelIdle(); canvas?.remove(); if (settled) cleanupCanvas(); };
  }, [pdf, pageNumber, zoom, dpr, runtime, generation, side, revision, priority, onSized, retry]);
  return <><div ref={host} />{error && <div className="empty-pane" role="alert">第 {pageNumber} 页载入失败：{error}<button onClick={() => setRetry(n => n + 1)}>重试</button></div>}<PdfHighlightLayer activeDiffId={activeDiffId} highlights={highlights} pageSize={pageSize} zoom={zoom} onActivateDiff={onActivateDiff} /></>;
}

function getEvidence(diff: DiffItem, side: "original" | "compare"): EvidenceBox[] {
  const display = diff.field_display_evidence?.[side] ?? diff.clause_display_evidence?.[side];
  if (diff.diff_type === "MODIFY" && display?.length) return display;
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

    const seen = new Set<string>();
    for (const evidence of evidences) {
      const key = JSON.stringify([evidence.page_no, evidence.method, evidence.highlight_type,
        ...[evidence.bbox.x0, evidence.bbox.y0, evidence.bbox.x1, evidence.bbox.y1].map(v => Math.round(v * 2))]);
      if (seen.has(key)) continue;
      seen.add(key);
      const type = evidence.highlight_type ?? diff.diff_type;
      const fallback = evidence.method === "block_fallback";
      const markKind = highlightMarkKind(evidence, fallback, diff);
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

function highlightMarkKind(evidence: EvidenceBox, fallback: boolean, diff: DiffItem): PageHighlight["markKind"] {
  const method = evidence.method || "";
  if (method === "comparison_observation" ||
      (method === "footer_visual_registered" && diff.content_recognition_reason === "VISUAL_FOOTER_CONTENT_UNVERIFIED") ||
      ["TABLE_STRUCTURE_UNRESOLVED", "TABLE_PRESENCE_UNKNOWN"].includes(String(diff.match_score_details?.decision_reason))) {
    return "observation";
  }
  if (fallback) {
    return "fallback";
  }
  const objectType = diff.match_score_details?.object_type;
  const sealObject = ['COMPANY_ROUND_SEAL', 'PERSONAL_NAME_SEAL', 'EDGE_SEAM_SEAL', 'OTHER_STAMP'].includes(String(objectType));
  if (diff.source_type === "seal" || sealObject || method === "seal_region" || diff.section_type === "signature:seal") {
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
  if (markKind === "seal" || current.diffId !== diffId || current.type !== type || current.fallback || fallback || current.markKind !== markKind) {
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
  onPageScroll?: (page: number, fraction: number, side: "original" | "compare") => void;
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
            aria-label={`${markKind === "observation" ? "定位待复核区域" : "定位差异"} ${highlight.diffId}`}
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
