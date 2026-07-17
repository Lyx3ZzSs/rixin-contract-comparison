import type { TaskStatus } from "../types";
import { toApiUrl } from "./api";
import { authorizedFetch } from "./authFetch";

export interface ProgressEvent {
  task_id: string;
  stage: string;
  progress_percent: number;
  status: TaskStatus;
  revision: number;
  detail?: Record<string, unknown>;
}

export interface ProgressEventStream {
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: ((error: unknown) => void) | null;
  close(): void;
}

export function createProgressEventSource(taskId: string): ProgressEventStream {
  const controller = new AbortController();
  let closed = false;
  let failed = false;
  const stream: ProgressEventStream = {
    onmessage: null,
    onerror: null,
    close() {
      closed = true;
      controller.abort();
    },
  };

  const fail = (error: unknown) => {
    if (!closed && !failed) {
      failed = true;
      stream.onerror?.(error);
    }
  };

  void (async () => {
    try {
      const response = await authorizedFetch(toApiUrl(`/api/compare/${taskId}/progress`), { signal: controller.signal });
      if (!response.ok || !response.body) throw new Error(`进度流连接失败 (${response.status})`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!closed) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = dispatchCompleteEvents(buffer, stream);
      }
      buffer += decoder.decode();
      buffer = dispatchCompleteEvents(buffer, stream);
      if (!closed) fail(new Error(buffer.trim() ? "进度流意外结束" : "进度流已结束"));
    } catch (error) {
      fail(error);
    }
  })();
  return stream;
}

function dispatchCompleteEvents(buffer: string, stream: ProgressEventStream): string {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const events = normalized.split("\n\n");
  const remainder = events.pop() ?? "";
  for (const rawEvent of events) {
    const data = rawEvent
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""));
    if (data.length > 0) stream.onmessage?.(new MessageEvent("message", { data: data.join("\n") }));
  }
  return remainder;
}
