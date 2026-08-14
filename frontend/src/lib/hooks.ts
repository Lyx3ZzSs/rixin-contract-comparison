import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { getDiffs, getTask, retryCompareTask } from "./api";
import { createProgressEventSource, type ProgressEvent, type ProgressEventStream } from "./api_sse";
import type { CompareRecordSummary, CompareTask, DiffItem } from "../types";

export type ProgressMode = "INITIAL_LOAD" | "SSE" | "POLLING" | "TERMINAL";

const FIRST_EVENT_TIMEOUT_MS = 3000;
const POLL_INITIAL_DELAY_MS = 1200;
const POLL_MAX_DELAY_MS = 4800;
const RESULT_COMPLETION_ANIMATION_MS = 1200;
const RECORD_COMPLETION_ANIMATION_MS = 900;
const PROGRESS_THROTTLE_MS = 350;

function normalizeRevision(value: unknown): number | null {
  const revision = typeof value === "number" ? value : Number.NaN;
  return Number.isFinite(revision) && revision >= 0 ? revision : null;
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
}

/** Trailing-edge throttle: coalesce rapid calls, delivering only the latest value. */
function throttleTrailing<T>(fn: (value: T) => void, delayMs: number): (value: T) => void {
  let pending: T | undefined;
  let timer: number | undefined;
  return (value: T) => {
    pending = value;
    if (timer !== undefined) return;
    timer = window.setTimeout(() => {
      timer = undefined;
      const latest = pending as T;
      pending = undefined;
      fn(latest);
    }, delayMs);
  };
}

interface ProgressSynchronizationOptions {
  taskId: string;
  skipInitialLoad?: boolean;
  onModeChange?: (mode: ProgressMode) => void;
  onTask: (task: CompareTask) => void;
  onProgress: (event: ProgressEvent) => void;
  onTerminalHint?: (event: ProgressEvent) => void;
  onTerminal: (task: CompareTask, source: "initial" | "poll", signal: AbortSignal) => void | Promise<void>;
  onError?: (error: unknown) => void;
}

interface ProgressSynchronization {
  stop(): void;
}

function startProgressSynchronization(options: ProgressSynchronizationOptions): ProgressSynchronization {
  const controller = new AbortController();
  const timers = new Set<number>();
  let stream: ProgressEventStream | null = null;
  let stopped = false;
  let mode: ProgressMode = "INITIAL_LOAD";
  let terminalHintRevision: number | null = null;
  let pollDelay = POLL_INITIAL_DELAY_MS;

  const transition = (next: ProgressMode) => {
    if (stopped || mode === next) return;
    mode = next;
    options.onModeChange?.(next);
  };

  const clearTimers = () => {
    for (const timer of timers) window.clearTimeout(timer);
    timers.clear();
  };

  const schedule = (callback: () => void, delay: number) => {
    if (stopped) return;
    const timer = window.setTimeout(() => {
      timers.delete(timer);
      if (!stopped) callback();
    }, delay);
    timers.add(timer);
  };

  const closeStream = () => {
    stream?.close();
    stream = null;
  };

  const schedulePoll = (delay = pollDelay) => {
    transition("POLLING");
    schedule(() => void loadTask("poll"), delay);
  };

  const fallBackToPolling = (immediate = false) => {
    closeStream();
    clearTimers();
    pollDelay = POLL_INITIAL_DELAY_MS;
    transition("POLLING");
    if (immediate) {
      void loadTask("poll");
    } else {
      schedulePoll(pollDelay);
    }
  };

  const openStream = () => {
    if (stopped) return;
    transition("SSE");
    stream = createProgressEventSource(options.taskId);
    schedule(() => fallBackToPolling(), FIRST_EVENT_TIMEOUT_MS);

    stream.onmessage = (message) => {
      if (stopped) return;
      try {
        const event = JSON.parse(message.data) as ProgressEvent;
        if (!event || typeof event !== "object" || !["PROCESSING", "COMPLETED", "FAILED"].includes(event.status)) {
          return;
        }
        clearTimers();
        if (event.status === "COMPLETED" || event.status === "FAILED") {
          const eventRevision = normalizeRevision(event.revision);
          if (eventRevision !== null) {
            terminalHintRevision = Math.max(terminalHintRevision ?? eventRevision, eventRevision);
          }
          options.onTerminalHint?.(event);
          fallBackToPolling(true);
          return;
        }
        options.onProgress(event);
      } catch {
        // Keepalives and malformed non-data frames do not change the state.
      }
    };

    stream.onerror = () => {
      if (!stopped) fallBackToPolling();
    };
  };

  async function loadTask(source: "initial" | "poll") {
    try {
      const task = await getTask(options.taskId, controller.signal);
      if (stopped) return;
      pollDelay = POLL_INITIAL_DELAY_MS;
      const terminal = task.status === "COMPLETED" || task.status === "FAILED";
      const taskRevision = normalizeRevision(task.revision);
      const revisionConfirmed = terminalHintRevision === null
        || (taskRevision !== null && taskRevision >= terminalHintRevision);
      if (terminal && revisionConfirmed) {
        clearTimers();
        closeStream();
        await options.onTerminal(task, source, controller.signal);
        if (stopped) return;
        transition("TERMINAL");
        return;
      }

      if (terminal) {
        // A terminal event is only a hint until the persisted revision catches up.
        schedulePoll();
        return;
      }

      options.onTask(task);
      if (source === "initial") {
        openStream();
      } else {
        schedulePoll();
      }
    } catch (error) {
      if (stopped || controller.signal.aborted) return;
      if (!isAbortError(error)) options.onError?.(error);
      pollDelay = Math.min(POLL_MAX_DELAY_MS, Math.max(POLL_INITIAL_DELAY_MS, pollDelay * 2));
      schedulePoll(pollDelay);
    }
  }

  if (options.skipInitialLoad) {
    openStream();
  } else {
    void loadTask("initial");
  }

  return {
    stop() {
      if (stopped) return;
      stopped = true;
      clearTimers();
      closeStream();
      controller.abort();
    },
  };
}

function abortableDelay(delay: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(done, delay);
    function done() {
      signal.removeEventListener("abort", cancel);
      resolve();
    }
    function cancel() {
      window.clearTimeout(timer);
      done();
    }
    signal.addEventListener("abort", cancel, { once: true });
  });
}

interface UseTaskProgressResult {
  task: CompareTask | null;
  diffs: DiffItem[];
  isLoading: boolean;
  error: string;
  progressMode: ProgressMode;
  isRetrying: boolean;
  retry: () => Promise<void>;
  setTask: Dispatch<SetStateAction<CompareTask | null>>;
  setDiffs: Dispatch<SetStateAction<DiffItem[]>>;
}

export function useTaskProgress(taskId: string): UseTaskProgressResult {
  const [task, setTask] = useState<CompareTask | null>(null);
  const [diffs, setDiffs] = useState<DiffItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [progressMode, setProgressMode] = useState<ProgressMode>("INITIAL_LOAD");
  const [isRetrying, setIsRetrying] = useState(false);
  const [synchronizationGeneration, setSynchronizationGeneration] = useState(0);

  useEffect(() => {
    setTask(null);
    setDiffs([]);
    setIsLoading(true);
    setError("");
    setProgressMode("INITIAL_LOAD");

    const synchronization = startProgressSynchronization({
      taskId,
      onModeChange: setProgressMode,
      onTask(nextTask) {
        setTask(nextTask);
        setError("");
        setIsLoading(false);
      },
      onProgress(progress) {
        setTask((current) => current ? {
          ...current,
          stage: progress.stage,
          progress_percent: progress.progress_percent,
          revision: Math.max(current.revision ?? 0, normalizeRevision(progress.revision) ?? 0),
        } : current);
      },
      onTerminalHint(progress) {
        setTask((current) => current ? {
          ...current,
          stage: "收尾完成中",
          progress_percent: progress.status === "COMPLETED" ? 100 : current.progress_percent,
        } : current);
      },
      async onTerminal(terminalTask, source, signal) {
        const terminalDiffs = terminalTask.status === "COMPLETED" ? await getDiffs(taskId, signal) : [];
        if (source === "poll" && terminalTask.status === "COMPLETED") {
          await abortableDelay(RESULT_COMPLETION_ANIMATION_MS, signal);
        }
        if (signal.aborted) return;
        setTask(terminalTask);
        setDiffs(terminalDiffs);
        setError("");
        setIsLoading(false);
      },
      onError(nextError) {
        setError(nextError instanceof Error ? nextError.message : "读取任务失败。");
        setIsLoading(false);
      },
    });

    return () => synchronization.stop();
  }, [taskId, synchronizationGeneration]);

  const retry = useCallback(async () => {
    setIsRetrying(true);
    setError("");
    try {
      await retryCompareTask(taskId);
      setSynchronizationGeneration((generation) => generation + 1);
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : "重试失败。");
    } finally {
      setIsRetrying(false);
    }
  }, [taskId]);

  return { task, diffs, isLoading, error, progressMode, isRetrying, retry, setTask, setDiffs };
}

export function useRecordProgressSSE(
  records: CompareRecordSummary[],
  onUpdate: (task: CompareTask | ProgressEvent) => void,
  onCompleted: () => void,
): void {
  const synchronizationsRef = useRef<Map<string, ProgressSynchronization>>(new Map());
  const onUpdateRef = useRef(onUpdate);
  const onCompletedRef = useRef(onCompleted);
  onUpdateRef.current = onUpdate;
  onCompletedRef.current = onCompleted;
  // Coalesce high-frequency progress events into one state update per interval; completion is unaffected.
  const throttledProgressUpdate = useRef(
    throttleTrailing((progress: ProgressEvent) => onUpdateRef.current(progress), PROGRESS_THROTTLE_MS),
  ).current;

  const processingKey = useMemo(() => records
    .filter((record) => record.status === "PROCESSING")
    .map((record) => record.task_id)
    .sort()
    .join(","), [records]);

  useEffect(() => {
    const active = synchronizationsRef.current;
    const processingIds = new Set(processingKey.split(",").filter(Boolean));

    for (const [taskId, synchronization] of active) {
      if (!processingIds.has(taskId)) {
        synchronization.stop();
        active.delete(taskId);
      }
    }

    for (const taskId of processingIds) {
      if (active.has(taskId)) continue;
      const synchronization = startProgressSynchronization({
        taskId,
        skipInitialLoad: true,
        onTask: (task) => onUpdateRef.current(task),
        onProgress: (progress) => throttledProgressUpdate(progress),
        onTerminalHint(progress) {
          onUpdateRef.current({ ...progress, status: "PROCESSING", stage: "收尾完成中" });
        },
        async onTerminal(task, _source, signal) {
          if (task.status === "COMPLETED") await abortableDelay(RECORD_COMPLETION_ANIMATION_MS, signal);
          if (signal.aborted) return;
          onUpdateRef.current(task);
          onCompletedRef.current();
        },
      });
      active.set(taskId, synchronization);
    }
  }, [processingKey]);

  useEffect(() => () => {
    for (const synchronization of synchronizationsRef.current.values()) synchronization.stop();
    synchronizationsRef.current.clear();
  }, []);
}
