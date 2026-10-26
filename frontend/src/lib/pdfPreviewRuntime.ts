import { PdfCanvasCache } from './pdfCanvasCache';
import { PdfRenderScheduler } from './pdfRenderScheduler';
import { previewPolicy } from './pdfPreviewTypes';
export class PdfPreviewRuntime {
  readonly scheduler = new PdfRenderScheduler({ maxConcurrent: previewPolicy.renderConcurrency, maxBackground: previewPolicy.backgroundConcurrency });
  readonly cache = new PdfCanvasCache(previewPolicy.idleCanvasBudgetBytes);
  private generation = 0;
  renders = 0;
  renderingBytes = 0;
  constructor(readonly taskId: string) {}
  nextGeneration() { return ++this.generation; }
  get stats() { return { ...this.scheduler.stats, ...this.cache.stats, displayBytes: Math.max(0, this.cache.stats.activeBytes - this.renderingBytes), renders: this.renders, renderingBytes: this.renderingBytes }; }
  publish() { window.dispatchEvent(new CustomEvent('pdf-preview:metrics', { detail: this.stats })); }
  async dispose() { this.cache.dispose(); await this.scheduler.dispose(); this.publish(); }
}
export function onPreviewIdle(run: () => void): () => void {
  if (typeof window.requestIdleCallback === 'function') { const id = window.requestIdleCallback(run, { timeout: 500 }); return () => window.cancelIdleCallback(id); }
  const id = window.setTimeout(run, 50); return () => window.clearTimeout(id);
}
// Default transport remains the fallback; alternate builds are measured before promotion.
export function previewTransport() {
  const mode = import.meta.env.VITE_PDF_TRANSPORT;
  return mode === 'range64' || mode === 'range256'
    ? { disableRange: false, disableStream: true, disableAutoFetch: true, rangeChunkSize: (mode === 'range64' ? 64 : 256) * 1024 }
    : {};
}
