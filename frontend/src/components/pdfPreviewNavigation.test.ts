import { expect, it } from 'vitest';
import { previewPageMetrics, previewAnchorTop } from './pdfPageScroll';
it('mixed page sizes and real gaps determine target offsets; zoom preserves page fraction',()=>{
 const pages=[{page_no:1,width_points:600,height_points:400,rotation:90 as const},{page_no:2,width_points:600,height_points:900,rotation:0 as const},{page_no:3,width_points:600,height_points:300,rotation:270 as const}];
 const metrics=previewPageMetrics(pages,.8,18);expect(metrics[1].offsetTop).toBe(338);
 expect(previewAnchorTop(metrics,2,.5)).toBe(698);
 expect(previewAnchorTop(previewPageMetrics(pages,1.6,18),2,.5)).toBe(1378);
 expect(previewAnchorTop(metrics,9,0)).toBeNull();
});
