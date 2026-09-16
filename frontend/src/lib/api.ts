import type {
  CompareRecordListResponse,
  CompareRecordQuery,
  CompareResponse,
  CompareQualitySummary,
  CompareTask,
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
): Promise<CompareResponse> {
  const formData = new FormData();
  formData.append("original_file", originalFile);
  formData.append("compare_file", compareFile);
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

export async function getEvents(taskId: string, signal?: AbortSignal): Promise<import('../types').DiffEventResponse> {
  return parseJsonResponse(await readAuthorizedFetch(`/api/compare/${taskId}/events`, signal));
}
export async function updateEventReview(taskId: string, eventId: string, revision: number, reviewStatus: import('../types').ReviewStatus): Promise<import('../types').DiffEventResponse> {
  return parseJsonResponse(await authorizedFetch(toApiUrl(`/api/compare/${taskId}/events/${eventId}/review`), {
    method: 'PUT', body: JSON.stringify({ revision, review_status: reviewStatus }), headers: { 'Content-Type': 'application/json' },
  }));
}

export async function getPreviewMetadata(taskId: string, signal?: AbortSignal): Promise<import('./pdfPreviewTypes').PreviewMetadata> {
  const data = await parseJsonResponse<import('./pdfPreviewTypes').PreviewMetadata>(await readAuthorizedFetch(`/api/compare/${encodeURIComponent(taskId)}/preview-metadata`, signal));
  if (data.schema_version !== 1 || data.coordinate_space !== 'display_pdf_points' || !['original', 'compare'].every(side => {
    const doc = data.documents?.[side as 'original' | 'compare'];
    return doc && typeof doc.source_revision === 'string' && doc.source_revision.length > 0 && Number.isInteger(doc.page_count) && doc.page_count > 0 && Array.isArray(doc.pages) && doc.pages.length === doc.page_count && doc.pages.every((page, i) => page.page_no === i + 1 && Number.isFinite(page.width_points) && page.width_points > 0 && Number.isFinite(page.height_points) && page.height_points > 0 && [0, 90, 180, 270].includes(page.rotation));
  })) throw new Error('PDF 页面尺寸数据无效');
  return data;
}
