export type PdfSide = 'original' | 'compare';
export interface PreviewPage { page_no: number; width_points: number; height_points: number; rotation: 0 | 90 | 180 | 270 }
export interface PreviewDocument { source_revision: string; page_count: number; pages: PreviewPage[] }
export interface PreviewMetadata { schema_version: 1; coordinate_space: 'display_pdf_points'; documents: Record<PdfSide, PreviewDocument> }
export interface RenderJob { key: string; generation: number; side: PdfSide; priority: 0 | 1 | 2; signal: AbortSignal; run: (signal: AbortSignal) => Promise<void> }
export const previewPolicy = { renderConcurrency: 2, backgroundConcurrency: 1, idleCanvasBudgetBytes: 96 * 1024 * 1024, neighborPages: 2 };
