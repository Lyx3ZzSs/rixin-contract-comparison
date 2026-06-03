import type {
  AuditItemReviewResponse,
  CompareRecordSummary,
  CompareResponse,
  CompareQualitySummary,
  CompareTask,
  DiffReviewPayload,
  DiffReviewResponse,
  DiffItem,
  ExtractionRecordSummary,
  ExtractionTaskResponse,
} from "../types";

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

export function getApiBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/+$/, "");
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
): Promise<CompareResponse> {
  const formData = new FormData();
  formData.append("original_file", originalFile);
  formData.append("compare_file", compareFile);

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

export async function getCompareRecords(): Promise<CompareRecordSummary[]> {
  const response = await fetch(toApiUrl("/api/compare/records"));
  const payload = await parseJsonResponse<{ records: CompareRecordSummary[] }>(response);
  return payload.records;
}

export async function getExtractionRecords(): Promise<ExtractionRecordSummary[]> {
  const response = await fetch(toApiUrl("/api/extract/records"));
  const payload = await parseJsonResponse<{ records: ExtractionRecordSummary[] }>(response);
  return payload.records;
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

export async function extractFields(
  file: File,
  fields: { id: string; name: string; type: string; description: string; semanticExtraction: boolean }[],
): Promise<ExtractionTaskResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append(
    "fields",
    JSON.stringify(
      fields.map((field) => ({
        id: field.id,
        name: field.name,
        type: "文本",
        description: field.description,
        semantic_extraction: field.semanticExtraction,
      })),
    ),
  );
  const response = await fetch(toApiUrl("/api/extract"), {
    method: "POST",
    body: formData,
  });
  return parseJsonResponse<ExtractionTaskResponse>(response);
}

export async function createExtractionPreview(file: File): Promise<Blob> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(toApiUrl("/api/extract/preview"), {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    let message = `预览失败 (${response.status})`;
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
  return response.blob();
}

export async function getExtractionTask(taskId: string): Promise<ExtractionTaskResponse> {
  const response = await fetch(toApiUrl(`/api/extract/${taskId}`));
  return parseJsonResponse<ExtractionTaskResponse>(response);
}
