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
  ExpectedDiff,
  QualityCaseDetail,
  QualityCaseExportRequest,
  QualityCaseExportResponse,
  QualityCaseListResponse,
  QualityRegressionRequest,
  QualityRunRequest,
  QualityRunResponse,
  QualityTaskReviewResponse,
} from "../types";

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

export function getApiBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL).replace(/\/+$/, "");
}

export function toApiUrl(path: string): string {
  if (!path) {
    return "";
  }
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  return `${getApiBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) {
        message = payload.detail;
      }
    } catch {
      // Keep the status based message when the server does not return JSON.
    }
    throw new Error(message);
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

  const response = await fetch(toApiUrl("/api/compare"), {
    method: "POST",
    body: formData,
  });
  return parseJsonResponse<CompareResponse>(response);
}

export async function getTask(taskId: string): Promise<CompareTask> {
  const response = await fetch(toApiUrl(`/api/compare/${taskId}`));
  return parseJsonResponse<CompareTask>(response);
}

export async function getCompareRecords(query: CompareRecordQuery = {}): Promise<CompareRecordListResponse> {
  const searchParams = new URLSearchParams();
  if (query.page !== undefined) searchParams.set("page", String(query.page));
  if (query.pageSize !== undefined) searchParams.set("page_size", String(query.pageSize));
  if (query.startDate) searchParams.set("start_date", query.startDate);
  if (query.endDate) searchParams.set("end_date", query.endDate);
  const queryString = searchParams.toString();
  const response = await fetch(toApiUrl(`/api/compare/records${queryString ? `?${queryString}` : ""}`));
  return parseJsonResponse<CompareRecordListResponse>(response);
}

export async function getDiffs(taskId: string): Promise<DiffItem[]> {
  const response = await fetch(toApiUrl(`/api/compare/${taskId}/diffs`));
  const payload = await parseJsonResponse<{ diffs: DiffItem[] }>(response);
  return payload.diffs;
}

export async function updateDiffReview(taskId: string, diffId: string, payload: DiffReviewPayload): Promise<DiffReviewResponse> {
  const response = await fetch(toApiUrl(`/api/compare/${taskId}/diffs/${diffId}/review`), {
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
  const response = await fetch(
    toApiUrl(`/api/compare/${taskId}/audit-items/${encodeURIComponent(auditItemId)}/review`),
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return parseJsonResponse<AuditItemReviewResponse>(response);
}

export async function getCompareQuality(taskId: string): Promise<CompareQualitySummary> {
  const response = await fetch(toApiUrl(`/api/compare/${taskId}/quality`));
  return parseJsonResponse<CompareQualitySummary>(response);
}

export async function listQualityCases(): Promise<QualityCaseListResponse> {
  const response = await fetch(toApiUrl("/api/quality/cases"));
  return parseJsonResponse<QualityCaseListResponse>(response);
}

export async function exportQualityCase(payload: QualityCaseExportRequest): Promise<QualityCaseExportResponse> {
  const response = await fetch(toApiUrl("/api/quality/cases/export"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<QualityCaseExportResponse>(response);
}

export async function getQualityCase(caseId: string): Promise<QualityCaseDetail> {
  const response = await fetch(toApiUrl(`/api/quality/cases/${encodeURIComponent(caseId)}`));
  return parseJsonResponse<QualityCaseDetail>(response);
}

export async function getQualityTaskReview(taskId: string): Promise<QualityTaskReviewResponse> {
  const response = await fetch(toApiUrl(`/api/quality/tasks/${encodeURIComponent(taskId)}/review`));
  return parseJsonResponse<QualityTaskReviewResponse>(response);
}

export async function updateQualityExpectedDiff(
  caseId: string,
  index: number,
  payload: Partial<ExpectedDiff>,
): Promise<QualityCaseDetail> {
  const response = await fetch(
    toApiUrl(`/api/quality/cases/${encodeURIComponent(caseId)}/expected-diffs/${index}`),
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return parseJsonResponse<QualityCaseDetail>(response);
}

export async function createQualityExpectedDiff(
  caseId: string,
  payload: Partial<ExpectedDiff>,
): Promise<QualityCaseDetail> {
  const response = await fetch(toApiUrl(`/api/quality/cases/${encodeURIComponent(caseId)}/expected-diffs`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<QualityCaseDetail>(response);
}

export async function deleteQualityExpectedDiff(caseId: string, index: number): Promise<QualityCaseDetail> {
  const response = await fetch(toApiUrl(`/api/quality/cases/${encodeURIComponent(caseId)}/expected-diffs/${index}`), {
    method: "DELETE",
  });
  return parseJsonResponse<QualityCaseDetail>(response);
}

export async function evaluateQuality(payload: QualityRunRequest): Promise<QualityRunResponse> {
  const response = await fetch(toApiUrl("/api/quality/evaluate"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<QualityRunResponse>(response);
}

export async function runQualityRegression(payload: QualityRegressionRequest): Promise<QualityRunResponse> {
  const response = await fetch(toApiUrl("/api/quality/regression"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<QualityRunResponse>(response);
}
