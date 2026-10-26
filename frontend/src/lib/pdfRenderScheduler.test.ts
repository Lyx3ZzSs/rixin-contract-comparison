import { describe, expect, it } from 'vitest';
import { PdfRenderScheduler } from './pdfRenderScheduler';
import type { RenderJob } from './pdfPreviewTypes';
const flush = async () => { for(let i=0;i<8;i++) await Promise.resolve(); };
function deferred(){let resolve!:()=>void;const promise=new Promise<void>(r=>{resolve=r});return {promise,resolve};}
describe('shared render scheduler',()=>{
 it('prioritizes target and visible across panes; bounds total and background concurrency',async()=>{
  const s=new PdfRenderScheduler({maxConcurrent:2,maxBackground:1});const done=deferred();const order:string[]=[];
  const add=(key:string,priority:0|1|2,side:'original'|'compare')=>s.enqueue({key,priority,side,generation:1,signal:new AbortController().signal,run:async()=>{order.push(key);await done.promise}});
  const jobs=[add('neighbor',2,'original'),add('visible',1,'compare'),add('target',0,'original'),add('neighbor2',2,'compare')];
  await flush();expect(order).toEqual(['target','visible']);expect(s.stats.running).toBe(2);done.resolve();await Promise.all(jobs);expect(s.stats.peakBackground).toBe(1);await s.dispose();
 });
 it('cancels generations, consumes failures and releases slots before disposal resolves',async()=>{
  const s=new PdfRenderScheduler({maxConcurrent:1,maxBackground:1});const outcomes:string[]=[];
  const job:RenderJob={key:'old',side:'original',generation:1,priority:1,signal:new AbortController().signal,run:signal=>new Promise((_,reject)=>signal.addEventListener('abort',()=>{outcomes.push('aborted');reject(new DOMException('cancel','AbortError'))}))};
  const first=s.enqueue(job).catch(e=>e.name);await flush();
  const queued=s.enqueue({...job,key:'queued'}).catch(e=>e.name);
  s.cancelGeneration(1);expect(await first).toBe('AbortError');expect(await queued).toBe('AbortError');
  await expect(s.enqueue({...job,key:'failure',generation:2,run:async()=>{throw Error('render failed')}})).rejects.toThrow('render failed');
  await s.enqueue({...job,key:'next',generation:2,run:async()=>{outcomes.push('next')}});await s.dispose();expect(outcomes).toEqual(['aborted','next']);expect(s.stats.running).toBe(0);expect(s.stats.queued).toBe(0);
 });
 it('external abort removes queued work and dispose rejects future work',async()=>{
  const s=new PdfRenderScheduler({maxConcurrent:1,maxBackground:1});const controller=new AbortController();
  const p=s.enqueue({key:'queued',side:'compare',generation:1,priority:2,signal:controller.signal,run:async()=>{throw Error('must not start')}}).catch(e=>e.name);controller.abort();expect(await p).toBe('AbortError');await s.dispose();
  await expect(s.enqueue({key:'late',side:'compare',generation:2,priority:0,signal:controller.signal,run:async()=>{}})).rejects.toMatchObject({name:'AbortError'});
 });
});
it('preempts background rendering to let a ready foreground page proceed',async()=>{
 const s=new PdfRenderScheduler({maxConcurrent:1,maxBackground:1});const order:string[]=[];
 const background=s.enqueue({key:'background',side:'original',generation:1,priority:2,signal:new AbortController().signal,run:signal=>new Promise((_,reject)=>{order.push('background');signal.addEventListener('abort',()=>reject(new DOMException('cancel','AbortError')));})}).catch(e=>e.name);
 await flush();await s.enqueue({key:'foreground',side:'compare',generation:2,priority:0,signal:new AbortController().signal,run:async()=>{order.push('foreground')}});
 expect(await background).toBe('AbortError');expect(order).toEqual(['background','foreground']);await s.dispose();
});
it('keeps neighboring pages idle while a visible page is still painting',async()=>{
 const s=new PdfRenderScheduler({maxConcurrent:2,maxBackground:1});const gate=deferred();const order:string[]=[];const signal=new AbortController().signal;
 const visible=s.enqueue({key:'visible',side:'original',generation:1,priority:1,signal,run:async()=>{order.push('visible');await gate.promise}});
 const neighbor=s.enqueue({key:'neighbor',side:'compare',generation:1,priority:2,signal,run:async()=>{order.push('neighbor')}});
 await flush();expect(order).toEqual(['visible']);gate.resolve();await Promise.all([visible,neighbor]);await s.dispose();
});
