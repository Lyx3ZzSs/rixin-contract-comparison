export type TaskStatus = "PROCESSING" | "COMPLETED" | "FAILED";
export type DiffType = "ADD" | "DELETE" | "MODIFY";
export type EvidenceQuality = "LOW" | "MEDIUM" | "HIGH";
export type ReviewStatus = "UNREVIEWED" | "CONFIRMED" | "FALSE_POSITIVE" | "NEEDS_REVIEW" | "IGNORED";

export interface ParseWarningDetail {
  code: string;
  message: string;
  severity: "INFO" | "WARNING" | "ERROR";
  page_no?: number | null;
  source: string;
}

export interface PageProfile {
  page_no: number;
  width: number;
  height: number;
  text_block_count: number;
  table_block_count: number;
  image_block_count: number;
  char_count: number;
  avg_confidence?: number | null;
  table_area_ratio: number;
  image_area_ratio: number;
  page_role: string;
  extraction_strategy: string;
  low_text: boolean;
  table_heavy: boolean;
}

export interface DocumentProfile {
  filename: string;
  page_count: number;
  extractor_used: string;
  total_text_chars: number;
  table_block_count: number;
  image_block_count: number;
  scanned_page_count: number;
  table_heavy_page_count: number;
  page_profiles: PageProfile[];
  recommended_strategy: string;
  warnings: ParseWarningDetail[];
}

export interface CompareResponse {
  task_id: string;
  status: TaskStatus;
  stage: string;
  progress_percent: number;
  diff_count: number;
  reviewed_count?: number;
  confirmed_count?: number;
  false_positive_count?: number;
  manual_review_count?: number;
  ignored_count?: number;
  audit_item_reviews?: Record<string, AuditItemReview>;
  extractor_used?: string;
  parse_warnings?: string[];
  parse_warning_details?: ParseWarningDetail[];
  document_profiles?: Record<string, DocumentProfile>;
  debug_artifact_paths?: Record<string, string>;
  report_url: string;
  report_filename: string;
  original_pdf_url: string;
  compare_pdf_url: string;
  original_highlight_pdf_url: string;
  compare_highlight_pdf_url: string;
  errors: string[];
}

export interface CompareTask extends CompareResponse {
  created_at: string;
  updated_at: string;
  original_filename: string;
  compare_filename: string;
}

export interface CompareRecordSummary {
  task_id: string;
  status: TaskStatus;
  stage: string;
  progress_percent: number;
  created_at: string;
  updated_at: string;
  original_filename: string;
  compare_filename: string;
  diff_count: number;
  report_url: string;
}

export interface BBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface EvidenceBox {
  page_no: number;
  bbox: BBox;
  method: string;
  text: string;
  highlight_type?: DiffType;
  confidence?: number;
  evidence_quality?: EvidenceQuality;
}

export interface DiffItem {
  diff_id: string;
  diff_type: DiffType;
  clause_no: string;
  title: string;
  original_text: string;
  compare_text: string;
  original_snippet: string;
  compare_snippet: string;
  readable_change: string;
  source_type?: string;
  match_score?: number | null;
  match_method?: string;
  match_score_details?: Record<string, number>;
  match_candidates?: Record<string, unknown>[];
  review_flags?: string[];
  review_status?: ReviewStatus;
  review_comment?: string;
  reviewed_by?: string;
  reviewed_at?: string;
  original_evidence?: EvidenceBox[];
  compare_evidence?: EvidenceBox[];
}

export type ExtractionFieldStatus = "found" | "not_found" | "error";

export interface ExtractionFieldValue {
  field_id: string;
  field_name: string;
  value: string;
  confidence: number;
  source_snippet: string;
  status: ExtractionFieldStatus;
  extraction_method?: "explicit" | "semantic" | null;
}

export interface ExtractionTaskResponse {
  task_id: string;
  task_type: string;
  status: TaskStatus;
  stage: string;
  filename: string;
  file_url: string;
  extractor_used: string;
  fields: { id: string; name: string; type: string; description: string; semantic_extraction: boolean }[];
  results: ExtractionFieldValue[];
  errors: string[];
}

export interface ExtractionRecordSummary {
  task_id: string;
  task_type: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  filename: string;
  file_url: string;
  extractor_used: string;
  field_count: number;
  found_count: number;
  not_found_count: number;
  error_count: number;
}

export interface DiffReviewPayload {
  review_status: ReviewStatus;
  review_comment?: string;
  reviewed_by?: string;
}

export interface AuditItemReview {
  audit_item_id?: string;
  review_status: ReviewStatus;
  review_comment?: string;
  reviewed_by?: string;
  reviewed_at?: string;
}

export interface DiffReviewResponse {
  task_id: string;
  diff: DiffItem;
  review_stats: {
    reviewed_count: number;
    confirmed_count: number;
    false_positive_count: number;
    manual_review_count: number;
    ignored_count: number;
  };
}

export interface AuditItemReviewResponse {
  task_id: string;
  audit_item_id: string;
  audit_item_review: AuditItemReview;
  review_stats: DiffReviewResponse["review_stats"];
}

export interface QualityDiffItem {
  diff_id: string;
  title: string;
  diff_type: DiffType;
  source_type: string;
  match_score?: number | null;
  match_method: string;
  review_flags: string[];
  review_status: ReviewStatus;
}

export interface CompareQualitySummary {
  task_id: string;
  status: TaskStatus;
  diff_count: number;
  review_stats: DiffReviewResponse["review_stats"];
  source_counts: Record<string, number>;
  evidence_quality_counts: Record<EvidenceQuality, number>;
  document_profile_summary: Record<string, {
    filename: string;
    page_count: number;
    extractor_used: string;
    recommended_strategy: string;
    total_text_chars: number;
    scanned_page_count: number;
    table_heavy_page_count: number;
  }>;
  parse_warning_details: ParseWarningDetail[];
  low_confidence_diffs: QualityDiffItem[];
  low_similarity_diffs: QualityDiffItem[];
  debug_artifacts: Record<string, string>;
}
