import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ResultPage } from './ResultPage';
import type { CompareTask, DiffEventResponse } from '../types';
const mocks = vi.hoisted(() => ({getEvents:vi.fn(),review:vi.fn(),scroll:vi.fn(),scrollPage:vi.fn(),scrollRatio:vi.fn(),retry:vi.fn(),download:vi.fn(),task:null as Partial<CompareTask> | null,error:'',isLoading:false,completionAnimating:false}));
vi.mock('../lib/api',()=>({getEvents:mocks.getEvents,getPreviewMetadata:vi.fn().mockRejectedValue(new Error('metadata unavailable')),updateEventReview:mocks.review,toApiUrl:(s:string)=>s}));
vi.mock('../lib/hooks',()=>({useTaskProgress:()=>({task:mocks.task,error:mocks.error,isLoading:mocks.isLoading,retry:mocks.retry,isRetrying:false,completionAnimating:mocks.completionAnimating})}));
vi.mock('../lib/authFetch',()=>({downloadAuthenticatedFile:mocks.download}));
vi.mock('../components/PdfDocumentViewer', async()=>{const {forwardRef,useImperativeHandle}=await import('react');return {PdfDocumentViewer:forwardRef(function Viewer({diffs,side,syncEnabled,onPageScroll,onScrollRatio}:any,ref){
 useImperativeHandle(ref,()=>({scrollToDiff:mocks.scroll,scrollToPage:(...args:any[])=>mocks.scrollPage(side,...args),syncScrollFrom:(ratio:number)=>mocks.scrollRatio(side,ratio)}));
 return <div data-testid="viewer" onScroll={()=>{if(syncEnabled){if(onPageScroll)onPageScroll(2,.25,side);else onScrollRatio(.4,side)}}}>{diffs.length}</div>;
})}});
const item=(id:string)=>({diff_id:id,diff_type:'ADD',source_type:'clause',title:id,compare_text:'新增条款',original_text:'',original_evidence:[],compare_evidence:[{page_no:15,bbox:{x0:10,y0:10,x1:100,y1:30}}],review_status:'UNREVIEWED',quality_status:'NEEDS_REVIEW'});
const data={task_id:'x',revision:1,event_count:1,context_available:true,page_alignment:[],events:[{event_id:'E',raw_diff_ids:['a','b'],direction:'ADD',diff:{...item('E'),title:'连续新增内容'},children:[item('a'),item('b')],review_conflict:false}]} as unknown as DiffEventResponse;
beforeEach(()=>{
 vi.clearAllMocks();
 window.history.replaceState({}, '', '/tasks/x');
 mocks.task={status:'COMPLETED',revision:1,original_filename:'原.pdf',compare_filename:'新.pdf',original_pdf_url:'/o',compare_pdf_url:'/c'};
 mocks.error=''; mocks.isLoading=false; mocks.completionAnimating=false;
 mocks.getEvents.mockResolvedValue(data); mocks.review.mockResolvedValue(data); mocks.download.mockResolvedValue(undefined);
});
it('synchronizes legacy documents by reading progress in both directions and honors the checkbox',async()=>{
 mocks.getEvents.mockResolvedValue({...data,context_available:false,page_alignment:[]});
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 await screen.findByText('连续新增内容');
 const [original,compare]=screen.getAllByTestId('viewer');
 fireEvent.scroll(original);
 expect(mocks.scrollRatio).toHaveBeenLastCalledWith('compare',.4);
 fireEvent.scroll(compare);
 expect(mocks.scrollRatio).toHaveBeenLastCalledWith('original',.4);
 expect(mocks.scrollPage).not.toHaveBeenCalled();
 fireEvent.click(screen.getByLabelText('同步滚动'));
 mocks.scrollRatio.mockClear();fireEvent.scroll(original);fireEvent.scroll(compare);
 expect(mocks.scrollRatio).not.toHaveBeenCalled();
 fireEvent.click(screen.getByLabelText('同步滚动'));fireEvent.scroll(original);
 expect(mocks.scrollRatio).toHaveBeenCalledWith('compare',.4);
});
it('uses explicit page correspondence when available',async()=>{
 mocks.getEvents.mockResolvedValue({...data,page_alignment:[{status:'MATCHED',original_pages:[2],compare_pages:[3],alignment_score:.99}]});
 render(<ResultPage taskId="x" onBack={()=>{}}/>);await screen.findByText('连续新增内容');
 fireEvent.scroll(screen.getAllByTestId('viewer')[0]);
 expect(mocks.scrollPage).toHaveBeenCalledWith('compare',3,.25);
 expect(mocks.scrollRatio).not.toHaveBeenCalled();
});
it.each(['INSERTED','UNCERTAIN'])('does not substitute progress for an explicitly unpaired %s page',async(status)=>{
 mocks.getEvents.mockResolvedValue({...data,page_alignment:[{status,original_pages:[],compare_pages:[2],alignment_score:.3}]});
 render(<ResultPage taskId="x" onBack={()=>{}}/>);await screen.findByText('连续新增内容');
 fireEvent.scroll(screen.getAllByTestId('viewer')[1]);
 expect(mocks.scrollRatio).not.toHaveBeenCalled();expect(mocks.scrollPage).not.toHaveBeenCalled();
 expect(screen.getByRole('status')).toHaveTextContent('无可靠对应页');
});
it('counts a grouped change once, including after expand and filtering',async()=>{
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 expect(await screen.findByText('已筛选出 1 条')).toBeInTheDocument();
 fireEvent.click(screen.getByText('2 项明细'));
 expect(screen.getByText('已筛选出 1 条')).toBeInTheDocument();
 expect(screen.getAllByTestId('viewer').map(v=>v.textContent)).toEqual(['1','1']);
 fireEvent.change(screen.getByLabelText('类型筛选'),{target:{value:'ADD'}});
 expect(screen.getByText('连续新增内容')).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('类型筛选'),{target:{value:'DELETE'}});
 expect(screen.getByText('当前筛选无差异')).toBeInTheDocument();
});
it.each(['TABLE_STRUCTURE_UNRESOLVED','TABLE_PRESENCE_UNKNOWN'])('labels uncertain metadata ADD as review after event serialization (%s)',async(reason)=>{
 const uncertain={...data.events[0].diff,source_type:'metadata',quality_status:'NEEDS_REVIEW',match_score_details:{decision_reason:reason}};
 mocks.getEvents.mockResolvedValue({...data,events:[{...data.events[0],diff:uncertain}]});
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 await screen.findByText('连续新增内容');
 expect(document.querySelector('.event-badge')).toHaveTextContent('待复核');
});
it('locates children without changing parent event identity and submits a versioned group review',async()=>{
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 fireEvent.click(await screen.findByText('连续新增内容'));
 expect(mocks.scroll.mock.calls[0][0].diff_id).toBe('E');
 fireEvent.click(screen.getByText('2 项明细'));fireEvent.click(screen.getByText('a'));
 expect(mocks.scroll.mock.calls.at(-1)?.[0].diff_id).toBe('a');
 fireEvent.change(screen.getByLabelText('审核差异 1'),{target:{value:'CONFIRMED'}});
 await waitFor(()=>expect(mocks.review).toHaveBeenCalledWith('x','E',1,'CONFIRMED'));
});
it('locates both observed graphic regions without adding a change event',async()=>{
 mocks.getEvents.mockResolvedValue({...data,graphic_observations:[{side:'compare',page:8,bbox:[430,690,490,745],counterpart_page:8,counterpart_bbox:[431,722,488,777],decision:'UNOBSERVED',reason:'VISIBLE_COUNTERPART_CONTENT_UNRESOLVED'}]});
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 fireEvent.click(await screen.findByText('比对第 8 页 · 查看图形'));
 expect(mocks.scroll).toHaveBeenCalledTimes(2);
 expect(mocks.scroll.mock.calls[0][0].compare_evidence[0].page_no).toBe(8);
 expect(mocks.scroll.mock.calls[1][0].original_evidence[0].bbox.y0).toBe(722);
 expect(screen.getByText('已筛选出 1 条')).toBeInTheDocument();
 fireEvent.click(screen.getByLabelText('仅看待复核'));
 expect(screen.getByText('已筛选出 1 条')).toBeInTheDocument();
});

it.each(['', '?view=legacy', '?view=events'])('opens the unified result for current and bookmarked URLs (%s)', async (query) => {
 window.history.replaceState({}, '', `/tasks/x${query}`);
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 expect(await screen.findByText('连续新增内容')).toBeInTheDocument();
 expect(screen.queryByRole('link', {name:'原审计视图'})).not.toBeInTheDocument();
 expect(mocks.getEvents).toHaveBeenCalledWith('x', expect.any(AbortSignal));
});
it('shows historical detail and review conflicts without hiding saved conclusions',async()=>{
 mocks.getEvents.mockResolvedValue({...data,context_available:false,events:[{...data.events[0],review_conflict:true,diff:{...data.events[0].diff,review_status:'NEEDS_REVIEW'}}]});
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 expect(await screen.findByText('历史结果缺少已保存的分组上下文，当前按独立变化展示。')).toBeInTheDocument();
 expect(screen.getByText('成员审核结论不一致，请复核')).toBeInTheDocument();
 expect(screen.getByLabelText('审核差异 1')).toHaveValue('NEEDS_REVIEW');
});
it('downloads the event report from a legacy bookmark',async()=>{
 window.history.replaceState({}, '', '/tasks/x?view=legacy');
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 await screen.findByText('连续新增内容');
 fireEvent.click(screen.getByRole('button',{name:'导出报告'}));
 await waitFor(()=>expect(mocks.download).toHaveBeenCalledWith('/api/compare/x/events/report','合同差异分析报告.pdf'));
});
it('shows event loading errors',async()=>{
 mocks.getEvents.mockRejectedValue(new Error('读取差异失败'));
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 expect(await screen.findByRole('alert')).toHaveTextContent('读取差异失败');
 expect(screen.getByRole('button',{name:'导出报告'})).toBeDisabled();
});
it('shows task loading and not-found states with a return action',()=>{
 mocks.task=null; mocks.isLoading=true;
 const onBack=vi.fn();
 const {rerender}=render(<ResultPage taskId="x" onBack={onBack}/>);
 expect(screen.getByRole('heading',{name:'正在载入对比结果'})).toBeInTheDocument();
 mocks.isLoading=false; mocks.error='任务不存在';
 rerender(<ResultPage taskId="x" onBack={onBack}/>);
 expect(screen.getByRole('heading',{name:'无法打开对比结果'})).toBeInTheDocument();
 expect(screen.getByText('任务不存在')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'返回'}));
 expect(onBack).toHaveBeenCalledOnce();
});
it('shows processing progress',()=>{
 mocks.task={status:'PROCESSING',stage:'文档解析中',progress_percent:35};
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 expect(screen.getByRole('heading',{name:'文档解析中'})).toBeInTheDocument();
 expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow','35');
 expect(mocks.getEvents).not.toHaveBeenCalled();
});
it('previews published documents during processing without exposing unfinished differences or export',async()=>{
 mocks.task={status:'PROCESSING',stage:'文档解析中',progress_percent:35,preview_available:true,original_pdf_url:'/o',compare_pdf_url:'/c'};
 const {rerender}=render(<ResultPage taskId="x" onBack={()=>{}}/>);
 expect(screen.getAllByTestId('viewer')).toHaveLength(2);
 const original=screen.getAllByTestId('viewer')[0];
 expect(screen.getByRole('heading',{name:'文档预览'})).toBeInTheDocument();
 expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow','35');
 expect(screen.getByRole('button',{name:'导出报告'})).toBeDisabled();
 expect(screen.queryByText('当前筛选无差异')).not.toBeInTheDocument();
 expect(mocks.getEvents).not.toHaveBeenCalled();
 fireEvent.scroll(original);expect(mocks.scrollRatio).toHaveBeenCalledWith('compare',.4);
 mocks.task={...mocks.task,status:'COMPLETED',revision:2};
 rerender(<ResultPage taskId="x" onBack={()=>{}}/>);
 expect(await screen.findByText('连续新增内容')).toBeInTheDocument();
 expect(screen.getAllByTestId('viewer')[0]).toBe(original);
 expect(screen.getByRole('button',{name:'导出报告'})).toBeEnabled();
});
it.each([
 ['EXECUTION_FAILED', undefined, true, '执行失败'],
 ['EXECUTION_FAILED', false, false, '执行失败'],
 ['SUBMISSION_FAILED', true, true, '提交失败'],
 ['SUBMISSION_FAILED', false, false, '提交失败'],
 ['CANCELLED', true, false, '已取消'],
] as const)('preserves failure and retry behavior for %s (%s)',(terminal_reason,retry_eligible,retryable,title)=>{
 mocks.task={status:'FAILED',terminal_reason,retry_eligible,errors:['处理已终止']};
 render(<ResultPage taskId="x" onBack={()=>{}}/>);
 expect(screen.getByRole('heading',{name:title})).toBeInTheDocument();
 expect(screen.getByText('处理已终止')).toBeInTheDocument();
 if(retryable){fireEvent.click(screen.getByRole('button',{name:'重试'}));expect(mocks.retry).toHaveBeenCalledOnce();}
 else expect(screen.queryByRole('button',{name:'重试'})).not.toBeInTheDocument();
});

it('requests events during completion animation and does not refetch or remount when it ends', async () => {
 mocks.completionAnimating=true; mocks.task={...mocks.task,progress_percent:100};
 const {rerender}=render(<ResultPage taskId="x" onBack={()=>{}}/>);
 expect(await screen.findByText('连续新增内容')).toBeInTheDocument();
 expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow','100');
 const viewer=screen.getAllByTestId('viewer')[0];
 const card=screen.getByText('连续新增内容');
 expect(mocks.getEvents).toHaveBeenCalledTimes(1);
 mocks.completionAnimating=false;
 rerender(<ResultPage taskId="x" onBack={()=>{}}/>);
 expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
 expect(screen.getAllByTestId('viewer')[0]).toBe(viewer);
 expect(screen.getByText('连续新增内容')).toBe(card);
 expect(mocks.getEvents).toHaveBeenCalledTimes(1);
});
