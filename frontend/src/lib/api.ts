import type {
  AuditItemReviewResponse,
  CompareRecordListResponse,
  CompareRecordQuery,
  CompareContractOptions,
  CompareResponse,
  CompareQualitySummary,
  CompareTask,
  DiffReviewPayload,
  DiffReviewResponse,
  DiffItem,
  TaskExecutionResponse,
} from "../types";
import {
  getAppBasePath,
  getConfiguredApiBasePath,
  getLegacyApiBaseUrl,
  normalizeBaseUrl,
} from "./env";
import { ApiError, authorizedFetch } from "./authFetch";

export const READ_REQUEST_TIMEOUT_MS = 15_000;

export function getApiBaseUrl(): string {
  return getLegacyApiBaseUrl() ?? getConfiguredApiBasePath() ?? normalizeBaseUrl(`${getAppBasePath()}/api`);
}

export function toApiUrl(path: string): string {
  if (!path) {
    return "";
  }
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  const apiBaseUrl = getApiBaseUrl();
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const endpointPath = apiBaseUrl.endsWith("/api") ? normalizedPath.replace(/^\/api(?=\/|$)/, "") : normalizedPath;
  return `${apiBaseUrl}${endpointPath}`;
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = new ApiError(response.status).message;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) {
        message = payload.detail;
      }
    } catch {
      // Keep the status based message when the server does not return JSON.
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

export async function compareContracts(
  originalFile: File,
  compareFile: File,
  options?: CompareContractOptions,
): Promise<CompareResponse> {
  const formData = new FormData();
  formData.append("original_file", originalFile);
  formData.append("compare_file", compareFile);
  if (options?.ignoreStamps) {
    formData.append("ignore_stamps", "true");
  }
  if (options?.ignoreHeadersFooters) {
    formData.append("ignore_headers_footers", "true");
  }
  if (options?.signingRegionMode) {
    formData.append("signing_region_mode", options.signingRegionMode);
  }

  const response = await authorizedFetch(toApiUrl("/api/compare"), {
    method: "POST",
    body: formData,
  });
  return parseJsonResponse<CompareResponse>(response);
}

export async function getTask(taskId: string, signal?: AbortSignal): Promise<CompareTask> {
  const response = await readAuthorizedFetch(`/api/compare/${taskId}`, signal);
  return parseJsonResponse<CompareTask>(response);
}

export async function retryCompareTask(taskId: string): Promise<TaskExecutionResponse> {
  const response = await authorizedFetch(toApiUrl(`/api/compare/${taskId}/retry`), { method: "POST" });
  return parseJsonResponse<TaskExecutionResponse>(response);
}

export async function getCompareRecords(query: CompareRecordQuery = {}, signal?: AbortSignal): Promise<CompareRecordListResponse> {
  const searchParams = new URLSearchParams();
  if (query.page !== undefined) searchParams.set("page", String(query.page));
  if (query.pageSize !== undefined) searchParams.set("page_size", String(query.pageSize));
  if (query.startDate) searchParams.set("start_date", query.startDate);
  if (query.endDate) searchParams.set("end_date", query.endDate);
  const queryString = searchParams.toString();
  const response = await readAuthorizedFetch(`/api/compare/records${queryString ? `?${queryString}` : ""}`, signal);
  return parseJsonResponse<CompareRecordListResponse>(response);
}

export async function getDiffs(taskId: string, signal?: AbortSignal): Promise<DiffItem[]> {
  const response = await readAuthorizedFetch(`/api/compare/${taskId}/diffs`, signal);
  const payload = await parseJsonResponse<{ diffs: DiffItem[] }>(response);
  return payload.diffs;
}

export async function updateDiffReview(taskId: string, diffId: string, payload: DiffReviewPayload): Promise<DiffReviewResponse> {
  const response = await authorizedFetch(toApiUrl(`/api/compare/${taskId}/diffs/${diffId}/review`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<DiffReviewResponse>(response);
}

export async function updateAuditItemReview(
  taskId: string,
  auditItemId: string,
  payload: DiffReviewPayload,
): Promise<AuditItemReviewResponse> {
  const response = await authorizedFetch(
    toApiUrl(`/api/compare/${taskId}/audit-items/${encodeURIComponent(auditItemId)}/review`),
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return parseJsonResponse<AuditItemReviewResponse>(response);
}

export async function getCompareQuality(taskId: string, signal?: AbortSignal): Promise<CompareQualitySummary> {
  const response = await readAuthorizedFetch(`/api/compare/${taskId}/quality`, signal);
  return parseJsonResponse<CompareQualitySummary>(response);
}

function readAuthorizedFetch(path: string, signal?: AbortSignal): Promise<Response> {
  return authorizedFetch(
    toApiUrl(path),
    signal ? { signal } : undefined,
    { timeoutMs: READ_REQUEST_TIMEOUT_MS },
  );
}
