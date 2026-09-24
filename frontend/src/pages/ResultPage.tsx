import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Download, Minus, Plus } from 'lucide-react';
import { PdfDocumentViewer, type PdfDocumentViewerHandle } from '../components/PdfDocumentViewer';
import { ProgressRing } from '../components/ProgressRing';
import { canRetryTask, taskStatusLabel } from '../lib/taskStatus';
import { useTaskProgress } from '../lib/hooks';
import { getEvents, getPreviewMetadata, toApiUrl, updateEventReview } from '../lib/api';
import { downloadAuthenticatedFile } from '../lib/authFetch';
import { counterpart } from '../lib/eventAlignment';
import type { DiffEvent, DiffEventResponse, DiffItem, DiffType, ReviewStatus } from '../types';
import './eventResult.css';
import { PdfPreviewRuntime } from '../lib/pdfPreviewRuntime';
import type { PreviewMetadata } from '../lib/pdfPreviewTypes';

const labels = { ADD: '新增', DELETE: '删除', MODIFY: '修改' };
export function ResultPage({ taskId, onBack, accessToken = '' }: { taskId: string; onBack: () => void; accessToken?: string }) {
  const { task, error: taskError, isLoading, retry, isRetrying, completionAnimating } = useTaskProgress(taskId);
  const [runtime, setRuntime] = useState<PdfPreviewRuntime | null>(null);
  const [metadata, setMetadata] = useState<PreviewMetadata | null | undefined>(undefined);
  const processing = task?.status === 'PROCESSING';
  const previewReady = task?.status === 'COMPLETED' || Boolean(task?.preview_available);
  useEffect(() => { const next = new PdfPreviewRuntime(taskId); setRuntime(next); return () => { void next.dispose(); }; }, [taskId]);
  useEffect(() => {
    const controller = new AbortController(); setMetadata(undefined);
    if (previewReady) getPreviewMetadata(taskId, controller.signal).then(setMetadata).catch(() => { if (!controller.signal.aborted) setMetadata(null); });
    return () => controller.abort();
  }, [taskId, previewReady]);
  const [data, setData] = useState<DiffEventResponse | null>(null);
  const [error, setError] = useState('');
  const [active, setActive] = useState('');
  const [filter, setFilter] = useState<DiffType | 'ALL'>('ALL');
  const [element, setElement] = useState('ALL');
  const [needsReview, setNeedsReview] = useState(false);
  const [zoom, setZoom] = useState(0.8);
  const [sync, setSync] = useState(true);
  const [saving, setSaving] = useState(false);
  const [locationNote, setLocationNote] = useState('');
  const original = useRef<PdfDocumentViewerHandle>(null);
  const compare = useRef<PdfDocumentViewerHandle>(null);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setActive(''); setError('');
    if (task?.status === 'COMPLETED') getEvents(taskId, controller.signal).then(setData).catch(e => {
      if (!controller.signal.aborted) setError(e.message);
    });
    return () => controller.abort();
  }, [taskId, task?.status, task?.revision]);
  const events = useMemo(() => (data?.events ?? []).filter(e => (filter === 'ALL' || e.direction === filter) && (element === 'ALL' || e.diff.source_type === element) && (!needsReview || e.review_conflict || e.diff.quality_status === 'NEEDS_REVIEW' || e.diff.review_status === 'NEEDS_REVIEW')), [data, filter, element, needsReview]);
  const diffs = useMemo(() => events.map(e => e.diff), [events]);
  const hasPageAlignment = Boolean(data?.page_alignment.length);
  function locate(event: DiffEvent, child?: DiffItem) {
    setActive(event.event_id);
    const diff = child ?? event.diff;
    original.current?.scrollToDiff(diff); compare.current?.scrollToDiff(diff);
    const missing = !(diff.original_evidence ?? []).length ? 'original' : !(diff.compare_evidence ?? []).length ? 'compare' : null;
    if (missing) {
      const side = missing === 'original' ? 'compare' : 'original';
      const evidence = (diff[`${side}_evidence`] ?? [])[0];
      const page = evidence ? counterpart(data?.page_alignment ?? [], evidence.page_no, side) : null;
      if (page !== null) (missing === 'original' ? original : compare).current?.scrollToPage(page);
      setLocationNote(`${missing === 'original' ? '标准文档' : '比对文档'}无对应变化${page === null ? '，保留当前位置' : `，定位对应第 ${page} 页`}`);
    } else setLocationNote('');
  }
  function syncPage(page: number, fraction: number, side: 'original' | 'compare') {
    if (!sync) return;
    const target = counterpart(data?.page_alignment ?? [], page, side);
    if (target === null) { setLocationNote(`第 ${page} 页无可靠对应页，另一侧保持当前位置`); return; }
    setLocationNote('');
    (side === 'original' ? compare : original).current?.scrollToPage(target, fraction);
  }
  function syncRatio(ratio: number, side: 'original' | 'compare') {
    // Historical results may predate page alignment. Keep both previews linked in that case.
    if (!sync || hasPageAlignment) return;
    (side === 'original' ? compare : original).current?.syncScrollFrom(ratio);
  }
  async function review(event: DiffEvent, status: ReviewStatus) {
    if (!data) return;
    setSaving(true); setError('');
    try { setData(await updateEventReview(taskId, event.event_id, data.revision, status)); }
    catch (e) { setError(e instanceof Error ? e.message : '审核失败'); }
    finally { setSaving(false); }
  }
  async function exportEvents() {
    try { await downloadAuthenticatedFile(toApiUrl(`/api/compare/${taskId}/events/report`), task?.report_filename || '合同差异分析报告.pdf'); }
    catch (e) { setError(e instanceof Error ? e.message : '导出失败'); }
  }
  if (!task) return <section className="state-screen">
    <h1>{isLoading ? '正在载入对比结果' : '无法打开对比结果'}</h1>
    <p>{taskError || (isLoading ? `任务 ${taskId}` : '任务不存在。')}</p>
    <button onClick={onBack}>返回</button>
  </section>;
  if (processing && !previewReady) return <section className="state-screen">
    <h1>{task.stage || '处理中'}</h1>
    <div className="progress-ring-panel"><ProgressRing value={Math.max(0, Math.min(100, task.progress_percent || 0))} label={task.stage || '处理中'} size="large" /></div>
    {taskError && <p role="alert">{taskError}</p>}
    <button onClick={onBack}>返回</button>
  </section>;
  if (task.status === 'FAILED') return <section className="state-screen">
    <h1>{taskStatusLabel(task.status, task.terminal_reason)}</h1>
    <p>{taskError || task.errors?.join('；') || task.stage || '处理失败。'}</p>
    {canRetryTask(task.status, task.terminal_reason, task.retry_eligible) && <button className="primary-action" disabled={isRetrying} onClick={() => void retry()}>{isRetrying ? '重试中...' : '重试'}</button>}
    <button onClick={onBack}>返回</button>
  </section>;
  return <section className="event-result" aria-label="合同差异事件视图">
    <header className="event-toolbar"><button onClick={onBack} aria-label="返回"><ChevronLeft size={18} /></button><h1>{processing ? '文档预览' : '比对结果页'}</h1>
      <label><input type="checkbox" checked={sync} onChange={e => setSync(e.target.checked)} /> 同步滚动</label>
      <div className="event-zoom"><button aria-label="缩小" onClick={() => setZoom(z => Math.max(0.4, +(z - 0.1).toFixed(1)))}><Minus size={16} /></button><span>{Math.round(zoom * 100)}%</span><button aria-label="放大" onClick={() => setZoom(z => Math.min(1.6, +(z + 0.1).toFixed(1)))}><Plus size={16} /></button></div>
      <button onClick={() => void exportEvents()} disabled={processing || !data}><Download size={16} />导出报告</button>
    </header>
    {(error || taskError) && <p role="alert" className="event-alert">{error || taskError}</p>}
    {locationNote && <p role="status" className="event-location">{locationNote}</p>}
    <div className="event-workspace"><div className="event-documents">
      {(['original', 'compare'] as const).map(side => <div className="event-document" key={side}><header><strong>{side === 'original' ? '标准文档' : '比对文档'}</strong><span title={task[`${side}_filename`]}>{task[`${side}_filename`]}</span></header>{runtime && runtime.taskId === taskId && <PdfDocumentViewer runtime={runtime} metadata={metadata === null ? null : metadata?.documents[side]} ref={side === 'original' ? original : compare} side={side} title={side === 'original' ? '标准文档' : '比对文档'} src={toApiUrl(task[`${side}_pdf_url`])} accessToken={accessToken} diffs={diffs} zoom={zoom} activeDiffId={active} syncEnabled={sync} onScrollRatio={syncRatio} onPageScroll={hasPageAlignment ? syncPage : undefined} onActivateDiff={id => { const e = events.find(e => e.event_id === id); if(e) locate(e); }} />}</div>)}
    </div><aside className="event-list" aria-label="比对结果">
      {(processing || completionAnimating) && <div className={processing ? 'state-screen' : 'event-completion-animation'} aria-live="polite">
        <h2>{processing ? task.stage || '处理中' : '比对完成'}</h2>
        <ProgressRing value={Math.max(0, Math.min(100, task.progress_percent || 0))} label={processing ? task.stage || '处理中' : '比对完成'} size="large" />
        {processing && <p>可以先查看两份文档。差异检查完成后，结果会自动显示。</p>}
      </div>}
      {!processing && <>
      <header><h2>差异项 <span>{data?.event_count ?? '—'}</span></h2><div><button aria-label="上一条差异" disabled={events.findIndex(e => e.event_id === active) <= 0} onClick={() => locate(events[events.findIndex(e => e.event_id === active) - 1])}><ChevronLeft size={17}/></button><button aria-label="下一条差异" disabled={!events.length || events.findIndex(e => e.event_id === active) >= events.length - 1} onClick={() => locate(events[events.findIndex(e => e.event_id === active) + 1])}><ChevronRight size={17}/></button></div></header>
      <div className="event-filters"><select aria-label="元素筛选" value={element} onChange={e => setElement(e.target.value)}><option value="ALL">元素 · 全部</option><option value="clause">文本</option><option value="seal">印章</option><option value="table">表格</option><option value="graphic">图形</option><option value="metadata">封面 / 水印</option><option value="signing_region">签章区</option></select><select aria-label="类型筛选" value={filter} onChange={e => setFilter(e.target.value as typeof filter)}><option value="ALL">类型 · 全部</option>{(['ADD','DELETE','MODIFY'] as const).map(t => <option key={t} value={t}>{labels[t]}</option>)}</select><label><input type="checkbox" checked={needsReview} onChange={e => setNeedsReview(e.target.checked)} />仅看待复核</label></div>
      <p className="event-count">已筛选出 {events.length} 条 <small>共 {data?.event_count ?? 0} 项 · 展开不增计数</small></p>
      {data && !data.context_available && <p className="event-location">历史结果缺少已保存的分组上下文，当前按独立变化展示。</p>}
      {(data?.graphic_observations ?? []).some(o => o.decision === 'UNOBSERVED') && <details className="event-location"><summary>图形观察待核查（不计入差异数）</summary>{(data?.graphic_observations ?? []).filter(o => o.decision === 'UNOBSERVED').map((o,i) => <button key={i} className="event-child" onClick={() => { const viewer = (o.side === 'original' ? original : compare).current; const sample = data?.events[0]?.diff; if (sample && o.bbox?.length === 4) { const [x0,y0,x1,y1] = o.bbox; viewer?.scrollToDiff({ ...sample, original_evidence: [], compare_evidence: [], [`${o.side}_evidence`]: [{page_no:o.page,bbox:{x0,y0,x1,y1}}] }); } else viewer?.scrollToPage(o.page); if (sample && o.counterpart_page && o.counterpart_bbox?.length === 4) { const [x0,y0,x1,y1] = o.counterpart_bbox; const otherSide = o.side === 'original' ? 'compare' : 'original'; (otherSide === 'original' ? original : compare).current?.scrollToDiff({ ...sample, original_evidence: [], compare_evidence: [], [`${otherSide}_evidence`]: [{page_no:o.counterpart_page,bbox:{x0,y0,x1,y1}}] }); } setLocationNote('该区域已观察到图形，现有证据不足以确认内容变化'); }}>{o.side === 'original' ? '标准' : '比对'}第 {o.page} 页 · 查看图形</button>)}</details>}
      <div className="event-cards">{events.map((event, index) => <article key={event.event_id} className={`event-card ${event.direction.toLowerCase()} ${active === event.event_id ? 'selected' : ''}`}>
        <button className="event-select" onClick={() => locate(event)} aria-pressed={active === event.event_id}><span className="event-number">{index + 1}</span><span className="event-badge">{event.diff.quality_status === 'NEEDS_REVIEW' && ['TABLE_STRUCTURE_UNRESOLVED', 'TABLE_PRESENCE_UNKNOWN'].includes(String(event.diff.match_score_details?.decision_reason)) ? '待复核' : labels[event.direction]}</span><strong>{event.diff.title}</strong></button>
        <div className="event-pages">{(['original','compare'] as const).map(side => <span key={side}>{side === 'original' ? '标准' : '比对'}：{[...new Set((event.diff[`${side}_evidence`] ?? []).map(e => e.page_no))].join('、') || '无'} 页</span>)}</div>
        {event.diff.original_text && <p className="event-text original-text">{event.diff.original_text}</p>}{event.diff.compare_text && <p className="event-text compare-text">{event.diff.compare_text}</p>}
        {event.children.length > 1 && <details><summary>{event.children.length} 项明细</summary>{event.children.map(child => <button className="event-child" key={child.diff_id} onClick={() => locate(event, child)}>{child.clause_no} {child.title}<ChevronRight size={14}/></button>)}</details>}
        {(event.diff.quality_status === 'NEEDS_REVIEW' || event.review_conflict) && <p className="event-review-note">{event.review_conflict ? '成员审核结论不一致，请复核' : '部分内容识读或变化证据需复核'}</p>}
        <label className="event-review">审核<select aria-label={`审核差异 ${index + 1}`} disabled={saving} value={event.diff.review_status} onChange={e => void review(event, e.target.value as ReviewStatus)}><option value="UNREVIEWED">未审核</option><option value="CONFIRMED">确认差异</option><option value="FALSE_POSITIVE">误报</option><option value="NEEDS_REVIEW">待复核</option><option value="IGNORED">忽略</option></select></label>
      </article>)}{data && !events.length && <p className="event-empty">当前筛选无差异</p>}</div>
      </>}
    </aside></div>
  </section>;
}
