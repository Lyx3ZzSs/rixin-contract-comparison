export type TaskStatus = "PROCESSING" | "COMPLETED" | "FAILED";
export type DiffType = "ADD" | "DELETE" | "MODIFY";
export type EvidenceQuality = "LOW" | "MEDIUM" | "HIGH";
export type DiffQualityStatus = "NORMAL" | "NEEDS_REVIEW";
export type OcrQualityStatus =
  | "OK"
  | "LOW_TEXT_CONFIDENCE"
  | "LAYOUT_MISMATCH"
  | "READING_ORDER_RISK"
  | "TABLE_RISK"
  | "SEAL_OR_SIGNATURE_RISK"
  | "UNRELIABLE";
export type OcrQualitySide = "original" | "compare";
export type ReviewStatus = "UNREVIEWED" | "CONFIRMED" | "FALSE_POSITIVE" | "NEEDS_REVIEW" | "IGNORED";

export interface PageOcrQualityProfile {
  side: OcrQualitySide;
  page_no: number;
  status: OcrQualityStatus;
  score: number;
  reasons: string[];
  metrics: Record<string, number | string | boolean>;
  affected_diff_ids: string[];
}

export interface TaskOcrQualitySummary {
  status: OcrQualityStatus;
  requires_review: boolean;
  page_count_by_status: Record<string, number>;
  risk_page_count: number;
  affected_diff_count: number;
  profiles: PageOcrQualityProfile[];
}

export type OcrRemediationActionType =
  | "NO_ACTION"
  | "MARK_REVIEW"
  | "RELOCATE_EVIDENCE"
  | "REPAIR_TABLE"
  | "RETRY_OCR_PAGE"
  | "ESCALATE_MANUAL_REVIEW";

export type OcrRemediationStatus =
  | "PLANNED"
  | "SKIPPED"
  | "SUCCEEDED"
  | "FAILED"
  | "MANUAL_REVIEW_REQUIRED";

export type OcrRemediationSummaryStatus =
  | "OK"
  | "ACTIONS_PLANNED"
  | "MANUAL_REVIEW_REQUIRED";

export interface OcrRemediationAction {
  action_id: string;
  action_type: OcrRemediationActionType;
  reason: string;
  status: OcrRemediationStatus;
  side?: OcrQualitySide | null;
  page_no?: number | null;
  diff_id?: string | null;
  before_quality: Record<string, unknown>;
  after_quality: Record<string, unknown>;
  changed_evidence: boolean;
  changed_diff_text: boolean;
  review_flags_added: string[];
  notes: string[];
}

export interface TaskOcrRemediationSummary {
  status: OcrRemediationSummaryStatus;
  requires_manual_review: boolean;
  attempted_action_count: number;
  successful_action_count: number;
  unresolved_action_count: number;
  risk_reduced_page_count: number;
  risk_reduced_diff_count: number;
  manual_review_required_count: number;
  actions: OcrRemediationAction[];
}

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
  ocr_quality_summary?: TaskOcrQualitySummary | null;
  ocr_remediation_summary?: TaskOcrRemediationSummary | null;
  debug_artifact_paths?: Record<string, string>;
  report_url: string;
  report_filename: string;
  original_pdf_url: string;
  compare_pdf_url: string;
  original_highlight_pdf_url: string;
  compare_highlight_pdf_url: string;
  errors: string[];
}

export interface CompareContractOptions {
  ignoreStamps: boolean;
  ignoreHeadersFooters: boolean;
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

export interface CompareRecordListResponse {
  records: CompareRecordSummary[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface CompareRecordQuery {
  page?: number;
  pageSize?: number;
  startDate?: string;
  endDate?: string;
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
  text_confidence?: number | null;
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
  section_type?: string;
  match_score?: number | null;
  match_method?: string;
  match_score_details?: Record<string, number | string>;
  match_candidates?: Record<string, unknown>[];
  review_flags?: string[];
  quality_status?: DiffQualityStatus;
  text_confidence?: number | null;
  merged_sources?: string[];
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
  quality_status?: DiffQualityStatus;
  text_confidence?: number | null;
  merged_sources?: string[];
  review_status: ReviewStatus;
}

export interface CompareQualitySummary {
  task_id: string;
  status: TaskStatus;
  diff_count: number;
  review_stats: DiffReviewResponse["review_stats"];
  source_counts: Record<string, number>;
  needs_review_count?: number;
  review_flag_counts?: Record<string, number>;
  cross_source_merged_count?: number;
  ocr_quality_summary?: TaskOcrQualitySummary | null;
  ocr_remediation_summary?: TaskOcrRemediationSummary | null;
  ocr_remediation_action_count?: number;
  ocr_remediation_unresolved_count?: number;
  manual_review_required_count?: number;
  ocr_risk_page_count?: number;
  ocr_affected_diff_count?: number;
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

export type GoldReviewStatus = "DRAFT" | "APPROVED" | "REJECTED";
export type GoldDatasetSplit = "dev" | "regression" | "holdout" | "adversarial" | "legacy";

export interface QualityCaseSummary {
  case_id: string;
  schema_version: string;
  dataset_split: GoldDatasetSplit | string;
  case_tags: string[];
  baseline_required: boolean;
  source_task_id: string;
  original_filename: string;
  compare_filename: string;
  approved_expected_count: number;
  draft_expected_count: number;
  rejected_expected_count: number;
  actual_diff_count: number;
  has_actual_json: boolean;
  has_source_pdfs: boolean;
}

export interface QualityActualDiffSummary {
  diff_id: string;
  diff_type: DiffType | string;
  source_type: string;
  title: string;
  quality_status: DiffQualityStatus | string;
  review_flags: string[];
}

export interface ExpectedEvidenceItem {
  side?: OcrQualitySide | string;
  page_no?: number;
  page?: number;
  text?: string;
  text_contains?: string;
  bbox?: BBox;
}

export interface ExpectedDiff {
  diff_type?: DiffType | string;
  source_type?: string;
  title_contains?: string;
  original_contains?: string;
  compare_contains?: string;
  review_status?: GoldReviewStatus;
  reviewer?: string;
  reviewed_at?: string;
  severity?: string;
  notes?: string;
  false_positive_reason?: string;
  false_negative_reason?: string;
  should_not_match_again?: boolean;
  source_actual_diff_id?: string;
  expected_evidence?: ExpectedEvidenceItem[];
}

export interface QualityCaseDetail {
  summary: QualityCaseSummary;
  readme: string;
  expected: {
    case_id?: string;
    expected_diffs?: ExpectedDiff[];
    [key: string]: unknown;
  };
  actual_diffs: QualityActualDiffSummary[];
}

export interface QualityCaseListResponse {
  cases: QualityCaseSummary[];
}

export interface QualityCaseExportRequest {
  task_id: string;
  case_id: string;
  force?: boolean;
}

export interface QualityCaseExportResponse {
  case_id: string;
  task_id: string;
  expected_diff_count: number;
  actual_diff_count: number;
}

export interface QualityTaskReviewDiff {
  diff_id: string;
  diff_type: DiffType | string;
  source_type: string;
  title: string;
  quality_status?: DiffQualityStatus | string;
  review_flags: string[];
  match_score?: number | null;
  original_snippet: string;
  compare_snippet: string;
}

export interface QualityTaskSuppressedDiff extends QualityTaskReviewDiff {
  suppression_reason: string;
  quality_decisions: string[];
}

export interface QualityDecisionSummary {
  diff_id: string;
  action: string;
  detail: Record<string, unknown>;
}

export interface QualityDebugArtifactSummary {
  has_diff_quality: boolean;
  has_diff_decisions: boolean;
  has_ocr_quality: boolean;
  has_clause_matches: boolean;
}

export interface QualityTaskReviewResponse {
  task_id: string;
  status: string;
  original_filename: string;
  compare_filename: string;
  historical_diff_count: number;
  retained_diff_count: number;
  suppressed_diff_count: number;
  ocr_quality_summary: Record<string, unknown>;
  retained_diffs: QualityTaskReviewDiff[];
  suppressed_diffs: QualityTaskSuppressedDiff[];
  quality_decisions: QualityDecisionSummary[];
  debug_artifacts: QualityDebugArtifactSummary;
}

export interface QualityRunRequest {
  dataset_splits?: Array<GoldDatasetSplit | string>;
  run_id?: string;
}

export interface QualityRegressionRequest extends QualityRunRequest {
  baseline_name?: string;
}

export interface QualityRunResponse {
  run_id: string;
  status: string;
  report: Record<string, unknown>;
  comparison?: Record<string, unknown> | null;
}
