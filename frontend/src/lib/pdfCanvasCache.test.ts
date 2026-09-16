import { describe, expect, it } from 'vitest';
import { PdfCanvasCache, canvasKey } from './pdfCanvasCache';
const canvas=()=>{const c=document.createElement('canvas');c.width=10;c.height=10;return c};
describe('canvas ownership and LRU',()=>{
 it('borrows without copying, never evicts displayed canvas, and does not double count returns',()=>{
  const cache=new PdfCanvasCache(800);const a=canvas(),b=canvas(),c=canvas();cache.release('a',a);cache.release('b',b);
  expect(cache.acquire('a')).toBe(a);expect(cache.acquire('a')).toBeNull();cache.release('c',c);expect(a.width).toBe(10);
  cache.release('a',a);cache.release('a',a);expect(cache.stats.idleBytes).toBe(800);expect(b.width).toBe(0);expect(cache.acquire('b')).toBeNull();cache.dispose();expect(a.width).toBe(0);
 });
 it('rejects oversize entries and clears borrowed entries when they return after disposal',()=>{
  const cache=new PdfCanvasCache(100);const c=canvas();cache.release('large',c);expect(c.width).toBe(0);expect(cache.stats.idleBytes).toBe(0);
  const other=new PdfCanvasCache(400);const d=canvas();other.release('d',d);other.acquire('d');other.dispose();expect(d.width).toBe(10);other.release('d',d);expect(d.width).toBe(0);expect(other.stats.activeBytes).toBe(0);
 });
 it('keys distinguish source, side, rotation, scale and DPR; invalidation waits for borrowed canvas',()=>{
  const key=(revision='rev',side='original',zoom=.8,dpr=1,rotation=0)=>canvasKey('task',side,revision,1,zoom,dpr,rotation);
  expect(new Set([key(),key('rev2'),key('rev','compare'),key('rev','original',1.6),key('rev','original',.8,2),key('rev','original',.8,1,90)]).size).toBe(6);
  const cache=new PdfCanvasCache(800);const c=canvas();cache.release(key(),c);cache.acquire(key());cache.invalidateDocument('rev');expect(c.width).toBe(10);cache.release(key(),c);expect(c.width).toBe(0);
 });
});
it('accounts for both canvases while cancellation overlaps a replacement with the same key',()=>{
 const cache=new PdfCanvasCache(800);const old=canvas(),next=canvas();cache.track('same',old);cache.track('same',next);expect(cache.stats.activeBytes).toBe(800);cache.discard('same',old);expect(cache.stats.activeBytes).toBe(400);cache.release('same',next);expect(cache.stats.activeBytes).toBe(0);expect(cache.stats.idleBytes).toBe(400);cache.dispose();
});
