import { act, fireEvent, render, waitFor, screen } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PdfDocumentViewer, type PdfDocumentViewerHandle } from './PdfDocumentViewer';
import { PdfPreviewRuntime } from '../lib/pdfPreviewRuntime';
const mocks=vi.hoisted(()=>({getDocument:vi.fn(),render:vi.fn()}));
vi.mock('pdfjs-dist/legacy/build/pdf.mjs',()=>({GlobalWorkerOptions:{},getDocument:mocks.getDocument}));
vi.mock('pdfjs-dist/legacy/build/pdf.worker.mjs?url',()=>({default:'worker'}));
const pages=Array.from({length:30},(_,i)=>({page_no:i+1,width_points:600,height_points:i%2?400:900,rotation:0 as const}));
const metadata={source_revision:'one',page_count:30,pages};
let runtime:PdfPreviewRuntime;
beforeEach(()=>{
 runtime=new PdfPreviewRuntime('test');mocks.render.mockReset().mockImplementation(()=>({promise:Promise.resolve(),cancel:vi.fn()}));
 mocks.getDocument.mockReturnValue({promise:Promise.resolve({numPages:30,getPage:async(n:number)=>({rotate:0,getViewport:({scale}:{scale:number})=>({width:600*scale,height:pages[n-1].height_points*scale}),render:()=>mocks.render(n)})}),destroy:vi.fn()});
 vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue({} as CanvasRenderingContext2D);
 vi.spyOn(HTMLElement.prototype,'offsetHeight','get').mockImplementation(function(this:HTMLElement){return parseFloat(this.style.height)||0});
 vi.spyOn(HTMLElement.prototype,'offsetTop','get').mockImplementation(function(this:HTMLElement){let top=0;let p=this.previousElementSibling as HTMLElement|null;while(p){top+=(parseFloat(p.style.height)||0)+18;p=p.previousElementSibling as HTMLElement|null}return top});
 vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(400);
});
afterEach(async()=>{await runtime.dispose();vi.restoreAllMocks()});
function setup(metadataReady=true){
 const ref=createRef<PdfDocumentViewerHandle>();const ready=vi.fn();
 const props={runtime,metadata:metadataReady?metadata:undefined,side:'compare' as const,src:'/pdf',accessToken:'',title:'比对',diffs:[],zoom:.8,activeDiffId:'',syncEnabled:false,onScrollRatio:vi.fn(),onActivateDiff:vi.fn(),onNavigationReady:ready};
 const view=render(<PdfDocumentViewer ref={ref} {...props}/>);return {...view,ref,ready,props};
}
it('returns cached distant pages without repainting and keeps the same page fraction across zoom',async()=>{
 const v=setup();await waitFor(()=>expect(v.container.querySelector('canvas[data-render-state="ready"]')).toBeTruthy());
 await act(async()=>v.ref.current!.scrollToPage(26,.3));await waitFor(()=>expect(v.ready).toHaveBeenCalledWith(26,'compare'));
 const scroller=v.container.querySelector('.pdf-scroll-shell')! as HTMLElement;
 const frame=v.container.querySelector('[data-page-number="26"]')! as HTMLElement;
 expect(scroller.scrollTop).toBeCloseTo(frame.offsetTop+frame.offsetHeight*.3);
 await act(async()=>v.ref.current!.scrollToPage(1));await waitFor(()=>expect(v.ready).toHaveBeenCalledWith(1,'compare'));
 const before=mocks.render.mock.calls.filter(c=>c[0]===26).length;
 await act(async()=>v.ref.current!.scrollToPage(26,.3));await waitFor(()=>expect(runtime.cache.hits).toBeGreaterThan(0));
 expect(mocks.render.mock.calls.filter(c=>c[0]===26).length).toBe(before);
 v.rerender(<PdfDocumentViewer ref={v.ref} {...v.props} zoom={1.6}/>);
 await waitFor(()=>expect(scroller.scrollTop).toBeCloseTo(frame.offsetTop+frame.offsetHeight*.3));v.unmount();
});
it('a cancelled older render never completes navigation or overwrites the newer target',async()=>{
 mocks.render.mockImplementation(n=>{if(n!==26)return {promise:Promise.resolve(),cancel:vi.fn()};let reject!:(e:unknown)=>void;return {promise:new Promise((_,r)=>{reject=r}),cancel:()=>reject({name:'RenderingCancelledException'})}});
 const v=setup();await waitFor(()=>expect(v.container.querySelector('canvas[data-render-state="ready"]')).toBeTruthy());
 await act(async()=>v.ref.current!.scrollToPage(26));await waitFor(()=>expect(mocks.render.mock.calls.some(c=>c[0]===26)).toBe(true));
 await act(async()=>v.ref.current!.scrollToPage(10));await waitFor(()=>expect(v.ready).toHaveBeenCalledWith(10,'compare'));
 expect(v.ready).not.toHaveBeenCalledWith(26,'compare');expect(v.container.querySelector('[data-page-number="26"] canvas')).toBeNull();v.unmount();
});
it('offers page retry after a real render failure and releases resources on exit',async()=>{
 mocks.render.mockImplementationOnce(()=>({promise:Promise.reject(Error('broken render')),cancel:vi.fn()}));
 const v=setup();expect(await screen.findByRole('button',{name:'重试'})).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'重试'}));await waitFor(()=>expect(v.container.querySelector('[data-page-number="1"] canvas[data-render-state="ready"]')).toBeTruthy());
 v.unmount();await runtime.dispose();expect(runtime.stats.activeBytes).toBe(0);expect(runtime.stats.idleBytes).toBe(0);expect(runtime.stats.running).toBe(0);
});
it('falls back to actual PDF geometry when the metadata endpoint fails',async()=>{
 const v=setup();v.rerender(<PdfDocumentViewer ref={v.ref} {...v.props} metadata={null}/>);
 await waitFor(()=>expect(v.container.querySelector('canvas[data-render-state="ready"]')).toBeTruthy());
 await act(async()=>v.ref.current!.scrollToPage(30,.1));await waitFor(()=>expect(v.ready).toHaveBeenCalledWith(30,'compare'));
 const f=v.container.querySelector('[data-page-number="30"]')! as HTMLElement;const s=v.container.querySelector('.pdf-scroll-shell')! as HTMLElement;
 expect(s.scrollTop).toBeCloseTo(f.offsetTop+f.offsetHeight*.1);expect(f.offsetHeight).toBe(320);v.unmount();
});
it('waits for preceding geometry before completing a target that has already rendered',async()=>{
 let release!:()=>void;const blocked=new Promise<void>(resolve=>{release=resolve});
 const doc=await mocks.getDocument().promise;const getPage=doc.getPage;
 doc.getPage=async(n:number)=>{if(n===5)await blocked;return getPage(n)};
 const v=setup(false);await waitFor(()=>expect(v.container.querySelector('canvas[data-render-state="ready"]')).toBeTruthy());
 await act(async()=>v.ref.current!.scrollToPage(26,.3));
 await waitFor(()=>expect(v.container.querySelector('[data-page-number="26"] canvas[data-render-state="ready"]')).toBeTruthy());
 await act(async()=>{await new Promise(resolve=>requestAnimationFrame(resolve))});
 expect(v.ready).not.toHaveBeenCalledWith(26,'compare');
 await act(async()=>release());await waitFor(()=>expect(v.ready).toHaveBeenCalledWith(26,'compare'));
 const frame=v.container.querySelector('[data-page-number="26"]')! as HTMLElement;
 expect((v.container.querySelector('.pdf-scroll-shell') as HTMLElement).scrollTop).toBeCloseTo(frame.offsetTop+frame.offsetHeight*.3);v.unmount();
});
it('does not report zoom navigation ready from the previous scale canvas',async()=>{
 const v=setup();await waitFor(()=>expect(v.container.querySelector('canvas[data-render-state="ready"]')).toBeTruthy());
 await act(async()=>v.ref.current!.scrollToPage(26,.3));await waitFor(()=>expect(v.ready).toHaveBeenCalledWith(26,'compare'));v.ready.mockClear();
 let release!:()=>void;const blocked=new Promise<void>(resolve=>{release=resolve});
 mocks.render.mockImplementation(n=>({promise:n===26?blocked:Promise.resolve(),cancel:vi.fn()}));
 v.rerender(<PdfDocumentViewer ref={v.ref} {...v.props} zoom={1.6}/>);
 await act(async()=>{await new Promise(resolve=>requestAnimationFrame(resolve))});
 expect(v.ready).not.toHaveBeenCalled();
 await act(async()=>release());await waitFor(()=>expect(v.ready).toHaveBeenCalledWith(26,'compare'));v.unmount();
});
it('releases render accounting even when PDF.js throws before returning a render task',async()=>{
 mocks.render.mockImplementationOnce(()=>{throw Error('synchronous render failure')});const v=setup();
 expect(await screen.findByRole('button',{name:'重试'})).toBeInTheDocument();v.unmount();await runtime.dispose();expect(runtime.stats.renderingBytes).toBe(0);
});
it('reopens a PDF when an established source revision changes',async()=>{
 const v=setup();await waitFor(()=>expect(v.container.querySelector('canvas[data-render-state="ready"]')).toBeTruthy());const loaded=mocks.getDocument.mock.calls.length;
 v.rerender(<PdfDocumentViewer ref={v.ref} {...v.props} metadata={{...metadata,source_revision:'replacement'}}/>);
 await waitFor(()=>expect(mocks.getDocument.mock.calls.length).toBe(loaded+1));v.unmount();
});

it('does not serialize PDF loading behind the first metadata response or reload it when metadata arrives',async()=>{
 const v=setup(false);await waitFor(()=>expect(v.container.querySelector('canvas[data-render-state="ready"]')).toBeTruthy());const loaded=mocks.getDocument.mock.calls.length;
 v.rerender(<PdfDocumentViewer ref={v.ref} {...v.props} metadata={metadata}/>);await act(async()=>{});expect(mocks.getDocument.mock.calls.length).toBe(loaded);v.unmount();
});
it('redraws at a new display DPR even when CSS viewport dimensions do not change',async()=>{
 let changed=()=>{};
 vi.stubGlobal('matchMedia',()=>({addEventListener:(_name:string,callback:()=>void)=>{changed=callback},removeEventListener:vi.fn()}));
 const v=setup();await waitFor(()=>expect(v.container.querySelector('canvas[data-render-state="ready"]')).toBeTruthy());
 vi.spyOn(window,'devicePixelRatio','get').mockReturnValue(2);await act(async()=>changed());
 await waitFor(()=>expect(v.container.querySelector('canvas[data-render-state="ready"]')?.getAttribute('width')).toBe('960'));v.unmount();vi.unstubAllGlobals();
});
