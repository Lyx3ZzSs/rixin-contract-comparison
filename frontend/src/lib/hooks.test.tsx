import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useTaskProgress } from './hooks';
import type { ProgressEventStream } from './api_sse';

const mocks = vi.hoisted(() => ({ getTask: vi.fn(), retry: vi.fn(), stream: vi.fn() }));
vi.mock('./api', () => ({ getTask: mocks.getTask, retryCompareTask: mocks.retry }));
vi.mock('./api_sse', () => ({ createProgressEventSource: mocks.stream }));
let stream: ProgressEventStream;
const processing = { task_id: 'x', status: 'PROCESSING', revision: 1, stage: '解析中', progress_percent: 20 };
const completed = { ...processing, status: 'COMPLETED', revision: 3, progress_percent: 100 };
beforeEach(() => {
 vi.resetAllMocks();
 stream = { onmessage: null, onerror: null, close: vi.fn() };
 mocks.stream.mockReturnValue(stream);
 mocks.getTask.mockResolvedValue(processing);
 mocks.retry.mockResolvedValue({ task_id: 'x', status: 'PROCESSING' });
});
afterEach(() => vi.useRealTimers());

it.each(['SSE', 'POLLING'])('publishes terminal data immediately while %s completion animation runs', async (mode) => {
 vi.useFakeTimers();
 const { result, unmount } = renderHook(() => useTaskProgress('x'));
 await act(async () => {});
 mocks.getTask.mockResolvedValue(completed);
 if (mode === 'SSE') {
  await act(async () => stream.onmessage?.(new MessageEvent('message', {data:JSON.stringify(completed)})));
 } else {
  await act(async () => stream.onerror?.(new Error('offline')));
  await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
 }
 expect(result.current.task?.status).toBe('COMPLETED');
 expect(result.current.completionAnimating).toBe(true);
 expect(result.current.progressMode).toBe('TERMINAL');
 await act(async () => { await vi.advanceTimersByTimeAsync(1199); });
 expect(result.current.completionAnimating).toBe(true);
 await act(async () => { await vi.advanceTimersByTimeAsync(1); });
 expect(result.current.completionAnimating).toBe(false);
 expect(mocks.getTask).toHaveBeenCalledTimes(2);
 unmount();
});

it('cancels the completion animation when navigating to another task', async () => {
 vi.useFakeTimers();
 const { result, rerender, unmount } = renderHook(({id}) => useTaskProgress(id), {initialProps:{id:'x'}});
 await act(async () => {});
 mocks.getTask.mockResolvedValue(completed);
 await act(async () => stream.onmessage?.(new MessageEvent('message', {data:JSON.stringify(completed)})));
 expect(result.current.completionAnimating).toBe(true);
 mocks.getTask.mockResolvedValue({...processing,task_id:'y'});
 rerender({id:'y'});
 await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
 expect(result.current.task?.task_id).toBe('y');
 expect(result.current.task?.status).toBe('PROCESSING');
 expect(result.current.completionAnimating).toBe(false);
 unmount();
});

it('loads completed task metadata without depending on the retired raw-diff request', async () => {
 mocks.getTask.mockResolvedValue(completed);
 const { result } = renderHook(() => useTaskProgress('x'));
 await waitFor(() => expect(result.current.task?.status).toBe('COMPLETED'));
 expect(result.current.isLoading).toBe(false);
 expect(result.current.progressMode).toBe('TERMINAL');
 expect(mocks.stream).not.toHaveBeenCalled();
});
it('waits for the persisted terminal revision and closes the stream', async () => {
 vi.useFakeTimers();
 const { result, unmount } = renderHook(() => useTaskProgress('x'));
 await act(async () => {});
 expect(result.current.progressMode).toBe('SSE');
 mocks.getTask.mockResolvedValueOnce({ ...completed, revision: 2 }).mockResolvedValue(completed);
 await act(async () => stream.onmessage?.(new MessageEvent('message', { data: JSON.stringify(completed) })));
 expect(result.current.task?.status).toBe('PROCESSING');
 expect(result.current.progressMode).toBe('POLLING');
 await act(async () => { await vi.advanceTimersByTimeAsync(2400); });
 expect(result.current.task?.status).toBe('COMPLETED');
 expect(result.current.task?.revision).toBe(3);
 expect(result.current.progressMode).toBe('TERMINAL');
 expect(stream.close).toHaveBeenCalled();
 unmount();
});
it('falls back to polling on stream failure and aborts pending work on unmount', async () => {
 vi.useFakeTimers();
 const { result, unmount } = renderHook(() => useTaskProgress('x'));
 await act(async () => {});
 await act(async () => stream.onerror?.(new Error('disconnected')));
 expect(result.current.progressMode).toBe('POLLING');
 await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
 expect(mocks.getTask).toHaveBeenCalledTimes(2);
 const signal = mocks.getTask.mock.calls[1][1] as AbortSignal;
 unmount();
 expect(signal.aborted).toBe(true);
 await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
 expect(mocks.getTask).toHaveBeenCalledTimes(2);
});
it('discovers published files before switching from upload polling to SSE', async () => {
 vi.useFakeTimers();
 mocks.getTask.mockResolvedValueOnce({...processing,preview_available:false})
  .mockResolvedValue({...processing,preview_available:true,revision:2});
 const {result,unmount}=renderHook(()=>useTaskProgress('x'));
 await act(async()=>{});
 expect(result.current.progressMode).toBe('POLLING');
 expect(mocks.stream).not.toHaveBeenCalled();
 await act(async()=>{await vi.advanceTimersByTimeAsync(1200);});
 expect(result.current.task?.preview_available).toBe(true);
 expect(result.current.progressMode).toBe('SSE');
 expect(mocks.stream).toHaveBeenCalledTimes(1);
 unmount();
});
it('reports read errors and reloads task state after a successful retry', async () => {
 mocks.getTask.mockRejectedValueOnce(new Error('读取失败'));
 const { result } = renderHook(() => useTaskProgress('x'));
 await waitFor(() => expect(result.current.error).toBe('读取失败'));
 expect(result.current.isLoading).toBe(false);
 await act(async () => { await result.current.retry(); });
 await waitFor(() => expect(result.current.task?.status).toBe('PROCESSING'));
 expect(mocks.retry).toHaveBeenCalledWith('x');
 expect(result.current.error).toBe('');
});
