import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { getCompareRecords, getDiffs, getTask } from "./api";
import { createProgressEventSource, type ProgressEvent } from "./api_sse";
import type { CompareRecordSummary, CompareTask, DiffItem, TaskStatus } from "../types";

const POLL_INTERVAL_MS = 1200;
const SSE_FALLBACK_DELAY_MS = 3000;
const RESULT_COMPLETION_ANIMATION_MS = 1200;
const RECORD_COMPLETION_ANIMATION_MS = 900;

interface UseTaskProgressResult {
  task: CompareTask | null;
  diffs: DiffItem[];
  isLoading: boolean;
  error: string;
  setTask: Dispatch<SetStateAction<CompareTask | null>>;
  setDiffs: Dispatch<SetStateAction<DiffItem[]>>;
}

export function useTaskProgress(taskId: string): UseTaskProgressResult {
  const [task, setTask] = useState<CompareTask | null>(null);
  const [diffs, setDiffs] = useState<DiffItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const sseFailedRef = useRef(false);

  // Initial load + polling fallback
  useEffect(() => {
    let isMounted = true;
    let timeoutId: number | undefined;

    async function loadTask() {
      try {
        const taskPayload = await getTask(taskId);
        if (!isMounted) return;
        setTask(taskPayload);
        if (taskPayload.status === "COMPLETED") {
          const diffPayload = await getDiffs(taskId);
          if (!isMounted) return;
          setDiffs(diffPayload);
        } else {
          setDiffs([]);
          if (taskPayload.status === "PROCESSING" && sseFailedRef.current) {
            timeoutId = window.setTimeout(loadTask, POLL_INTERVAL_MS);
          }
        }
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : "读取任务失败。");
        }
      } finally {
        if (isMounted) setIsLoading(false);
      }
    }

    void loadTask();

    return () => {
      isMounted = false;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [taskId]);

  // SSE stream — only when processing
  useEffect(() => {
    if (!task || task.status !== "PROCESSING") return;

    let isMounted = true;
    let fallbackTimer: number | undefined;
    let completionTimer: number | undefined;

    const eventSource = createProgressEventSource(taskId);

    fallbackTimer = window.setTimeout(() => {
      if (isMounted) {
        sseFailedRef.current = true;
        eventSource.close();
        // Trigger polling by reloading task
        void getTask(taskId).then((t) => {
          if (isMounted) setTask(t);
        });
      }
    }, SSE_FALLBACK_DELAY_MS);

    eventSource.onmessage = (event) => {
      if (fallbackTimer !== undefined) {
        window.clearTimeout(fallbackTimer);
        fallbackTimer = undefined;
      }
      if (!isMounted) return;

      try {
        const progress: ProgressEvent = JSON.parse(event.data);
        sseFailedRef.current = false;

        if (progress.status === "COMPLETED") {
          setTask((prev) =>
            prev ? { ...prev, status: "PROCESSING", stage: "收尾完成中", progress_percent: 100 } : prev,
          );
          const fullTaskPromise = getTask(taskId);
          const diffDataPromise = getDiffs(taskId);
          completionTimer = window.setTimeout(() => {
            void Promise.all([fullTaskPromise, diffDataPromise]).then(([fullTask, diffData]) => {
              if (!isMounted) return;
              setTask(fullTask);
              setDiffs(diffData);
            });
          }, RESULT_COMPLETION_ANIMATION_MS);
          eventSource.close();
        } else if (progress.status === "FAILED") {
          void getTask(taskId).then((fullTask) => {
            if (isMounted) setTask(fullTask);
          });
          eventSource.close();
        } else {
          setTask((prev) =>
            prev
              ? { ...prev, stage: progress.stage, progress_percent: progress.progress_percent }
              : prev,
          );
        }
      } catch {
        // Ignore parse errors for keepalive/comments
      }
    };

    eventSource.onerror = () => {
      if (fallbackTimer !== undefined) {
        window.clearTimeout(fallbackTimer);
      }
      if (isMounted) {
        sseFailedRef.current = true;
      }
      eventSource.close();
    };

    return () => {
      isMounted = false;
      if (fallbackTimer !== undefined) window.clearTimeout(fallbackTimer);
      if (completionTimer !== undefined) window.clearTimeout(completionTimer);
      eventSource.close();
    };
  }, [taskId, task?.status]);

  return { task, diffs, isLoading, error, setTask, setDiffs };
}

const SSE_FALLBACK_POLL_MS = 1800;

/**
 * 为 records 列表中 PROCESSING 状态的记录建立 SSE 连接，
 * 实时更新 progress_percent 和 stage。
 * SSE 不可用时回退到轮询。
 */
export function useRecordProgressSSE(
  records: CompareRecordSummary[],
  onUpdate: (taskId: string, progress: number, stage: string, status: TaskStatus) => void,
  onCompleted: () => void,
): void {
  const connectionsRef = useRef<Map<string, EventSource>>(new Map());
  const fallbackRef = useRef<Map<string, number>>(new Map());
  const completionRef = useRef<Map<string, number>>(new Map());
  const onUpdateRef = useRef(onUpdate);
  const onCompletedRef = useRef(onCompleted);
  onUpdateRef.current = onUpdate;
  onCompletedRef.current = onCompleted;

  // Stable key: only changes when the set of PROCESSING task IDs changes.
  // Progress updates (percent, stage) don't change this key, so SSE
  // connections survive across progress events.
  const processingKey = records
    .filter((r) => r.status === "PROCESSING")
    .map((r) => r.task_id)
    .sort()
    .join(",");

  useEffect(() => {
    const activeConnections = connectionsRef.current;
    const activeFallbacks = fallbackRef.current;
    const activeCompletions = completionRef.current;
    const processingIds = new Set(processingKey.split(",").filter(Boolean));

    // Close SSE for records that are no longer PROCESSING
    for (const [taskId, es] of activeConnections) {
      if (!processingIds.has(taskId)) {
        es.close();
        activeConnections.delete(taskId);
      }
    }
    // Clear fallback timers for records that are no longer PROCESSING
    for (const [taskId, timerId] of activeFallbacks) {
      if (!processingIds.has(taskId)) {
        window.clearTimeout(timerId);
        activeFallbacks.delete(taskId);
      }
    }
    for (const [taskId, timerId] of activeCompletions) {
      if (!processingIds.has(taskId)) {
        window.clearTimeout(timerId);
        activeCompletions.delete(taskId);
      }
    }

    const completeAfterRingAnimation = (taskId: string) => {
      const existingTimer = activeCompletions.get(taskId);
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer);
      }
      const timerId = window.setTimeout(() => {
        activeCompletions.delete(taskId);
        onCompletedRef.current();
      }, RECORD_COMPLETION_ANIMATION_MS);
      activeCompletions.set(taskId, timerId);
    };

    // Open SSE for new PROCESSING records
    for (const taskId of processingIds) {
      if (activeConnections.has(taskId)) continue;

      const eventSource = createProgressEventSource(taskId);
      activeConnections.set(taskId, eventSource);

      const scheduleFallback = () => {
        const fallbackTimer = window.setTimeout(function poll() {
          void getCompareRecords().then((list) => {
            const rec = list.find((r) => r.task_id === taskId);
            if (!rec) return;
            onUpdateRef.current(taskId, rec.progress_percent, rec.stage, rec.status);
            if (rec.status === "PROCESSING") {
              const next = window.setTimeout(poll, SSE_FALLBACK_POLL_MS);
              activeFallbacks.set(taskId, next);
            } else {
              onUpdateRef.current(taskId, 100, "收尾完成中", "PROCESSING");
              completeAfterRingAnimation(taskId);
              activeFallbacks.delete(taskId);
            }
          });
        }, SSE_FALLBACK_POLL_MS);
        activeFallbacks.set(taskId, fallbackTimer);
      };

      eventSource.onmessage = (event) => {
        try {
          const progress: ProgressEvent = JSON.parse(event.data);
          if (progress.status === "COMPLETED") {
            onUpdateRef.current(taskId, 100, "收尾完成中", "PROCESSING");
            eventSource.close();
            activeConnections.delete(taskId);
            completeAfterRingAnimation(taskId);
          } else if (progress.status === "FAILED") {
            onUpdateRef.current(taskId, progress.progress_percent, progress.stage, progress.status);
            eventSource.close();
            activeConnections.delete(taskId);
            onCompletedRef.current();
          } else {
            onUpdateRef.current(taskId, progress.progress_percent, progress.stage, progress.status);
          }
        } catch {
          // keepalive comments — ignore
        }
      };

      eventSource.onerror = () => {
        eventSource.close();
        activeConnections.delete(taskId);
        scheduleFallback();
      };
    }
  }, [processingKey]);

  // Unmount cleanup: close all connections
  useEffect(() => {
    return () => {
      for (const es of connectionsRef.current.values()) es.close();
      connectionsRef.current.clear();
      for (const timerId of fallbackRef.current.values()) window.clearTimeout(timerId);
      fallbackRef.current.clear();
      for (const timerId of completionRef.current.values()) window.clearTimeout(timerId);
      completionRef.current.clear();
    };
  }, []);
}
