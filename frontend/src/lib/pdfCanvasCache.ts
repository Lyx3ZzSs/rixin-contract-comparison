export const canvasKey = (task: string, side: string, revision: string, page: number, zoom: number, dpr: number, rotation: number) => JSON.stringify([task, side, revision, page, zoom, dpr, rotation]);
const bytes = (c: HTMLCanvasElement) => c.width * c.height * 4;
const free = (c: HTMLCanvasElement) => { c.remove(); c.width = 0; c.height = 0; };
export class PdfCanvasCache {
  private idle = new Map<string, HTMLCanvasElement>();
  private active = new Map<HTMLCanvasElement, string>();
  private invalid = new Set<string>();
  private closed = false;
  hits = 0;
  constructor(readonly budgetBytes = 96 * 1024 * 1024) {}
  get stats() { return { idleBytes: [...this.idle.values()].reduce((n, c) => n + bytes(c), 0), activeBytes: [...this.active.keys()].reduce((n, c) => n + bytes(c), 0), idleCount: this.idle.size, activeCount: this.active.size, hits: this.hits }; }
  acquire(key: string) {
    const canvas = this.idle.get(key);
    if (!canvas || [...this.active.values()].includes(key) || this.closed) return null;
    this.idle.delete(key); this.active.set(canvas, key); this.hits++;
    return canvas;
  }
  track(key: string, canvas: HTMLCanvasElement) { this.active.set(canvas, key); }
  release(key: string, canvas: HTMLCanvasElement) {
    this.active.delete(canvas);
    if (this.idle.get(key) === canvas) return;
    if (this.closed || this.invalid.has(key) || bytes(canvas) > this.budgetBytes || !canvas.width) { free(canvas); return; }
    const old = this.idle.get(key); if (old) free(old);
    canvas.remove(); this.idle.delete(key); this.idle.set(key, canvas);
    while (this.stats.idleBytes > this.budgetBytes) {
      const [oldKey, oldCanvas] = this.idle.entries().next().value!;
      this.idle.delete(oldKey); free(oldCanvas);
    }
  }
  discard(key: string, canvas: HTMLCanvasElement) {
    this.active.delete(canvas);
    if (this.idle.get(key) === canvas) this.idle.delete(key);
    free(canvas);
  }
  invalidateDocument(revision: string) {
    for (const [key, canvas] of this.idle) if (JSON.parse(key)[2] === revision) { this.idle.delete(key); free(canvas); }
    for (const key of this.active.values()) if (JSON.parse(key)[2] === revision) this.invalid.add(key);
  }
  dispose() { this.closed = true; this.idle.forEach(free); this.idle.clear(); }
}
