import type { RenderJob } from './pdfPreviewTypes';
const aborted = () => new DOMException('Render cancelled', 'AbortError');
interface Pending { job: RenderJob; controller: AbortController; resolve: () => void; reject: (e: unknown) => void; detach: () => void }
export class PdfRenderScheduler {
  private queue: Pending[] = [];
  private running = new Set<Pending>();
  private closed = false;
  private scheduled = false;
  private waiters: (() => void)[] = [];
  private peakRunning = 0;
  private peakBackground = 0;
  private cancelled = 0;
  constructor(private options = { maxConcurrent: 2, maxBackground: 1 }) {}
  get stats() { return { running: this.running.size, queued: this.queue.length, peakRunning: this.peakRunning, peakBackground: this.peakBackground, cancelled: this.cancelled }; }
  enqueue(job: RenderJob): Promise<void> {
    if (this.closed || job.signal.aborted) return Promise.reject(aborted());
    return new Promise((resolve, reject) => {
      const controller = new AbortController();
      const entry: Pending = { job, controller, resolve, reject, detach: () => job.signal.removeEventListener('abort', cancel) };
      const cancel = () => this.cancel(entry);
      job.signal.addEventListener('abort', cancel, { once: true });
      this.queue.push(entry);
      this.schedule();
    });
  }
  cancelGeneration(generation: number) {
    for (const entry of [...this.queue, ...this.running]) if (entry.job.generation === generation) this.cancel(entry);
  }
  private cancel(entry: Pending) {
    if (entry.controller.signal.aborted) return;
    this.cancelled++;
    entry.controller.abort();
    const index = this.queue.indexOf(entry);
    if (index >= 0) { this.queue.splice(index, 1); entry.detach(); entry.reject(aborted()); }
    this.schedule();
  }
  private schedule() {
    if (this.scheduled) return;
    this.scheduled = true;
    queueMicrotask(() => { this.scheduled = false; this.pump(); });
  }
  private pump() {
    this.queue.sort((a, b) => a.job.priority - b.job.priority);
    if (this.queue.some(e => e.job.priority < 2) && this.running.size >= this.options.maxConcurrent) {
      const background = [...this.running].find(e => e.job.priority === 2 && !e.controller.signal.aborted);
      if (background) this.cancel(background);
    }
    while (!this.closed && this.running.size < this.options.maxConcurrent) {
      const backgrounds = [...this.running].filter(e => e.job.priority === 2).length;
      const foregroundActive = [...this.running].some(e => e.job.priority < 2);
      const index = this.queue.findIndex(e => e.job.priority < 2 || !foregroundActive && backgrounds < this.options.maxBackground);
      if (index < 0) break;
      const [entry] = this.queue.splice(index, 1);
      this.running.add(entry);
      this.peakRunning = Math.max(this.peakRunning, this.running.size);
      this.peakBackground = Math.max(this.peakBackground, backgrounds + (entry.job.priority === 2 ? 1 : 0));
      Promise.resolve().then(() => {
        if (entry.controller.signal.aborted) throw aborted();
        return entry.job.run(entry.controller.signal);
      }).then(() => { if (entry.controller.signal.aborted) entry.reject(aborted()); else entry.resolve(); }, e => entry.reject(entry.controller.signal.aborted ? aborted() : e))
        .finally(() => { entry.detach(); this.running.delete(entry); this.schedule(); });
    }
    if (this.closed && !this.running.size) this.waiters.splice(0).forEach(resolve => resolve());
  }
  async dispose() {
    this.closed = true;
    for (const entry of [...this.queue, ...this.running]) this.cancel(entry);
    if (this.running.size) await new Promise<void>(resolve => this.waiters.push(resolve));
  }
}
