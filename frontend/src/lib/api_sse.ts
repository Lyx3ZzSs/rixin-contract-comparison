import { toApiUrl } from "./api";
import type { TaskStatus } from "../types";

export interface ProgressEvent {
  task_id: string;
  stage: string;
  progress_percent: number;
  status: TaskStatus;
  detail?: Record<string, unknown>;
}

export function createProgressEventSource(taskId: string): EventSource {
  return new EventSource(toApiUrl(`/api/compare/${taskId}/progress`));
}
