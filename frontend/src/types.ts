export type TaskStatus = "PROCESSING" | "COMPLETED" | "FAILED";
export type DiffType = "ADD" | "DELETE" | "MODIFY";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";

export interface CompareResponse {
  task_id: string;
  status: TaskStatus;
  diff_count: number;
  high_risk_count: number;
  medium_risk_count: number;
  low_risk_count: number;
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
  ai_summary: string;
  report_ai_analysis?: ReportAIAnalysis | null;
  original_page_screenshots?: string[];
  compare_page_screenshots?: string[];
}

export interface CompareRecordSummary {
  task_id: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  original_filename: string;
  compare_filename: string;
  diff_count: number;
  high_risk_count: number;
  medium_risk_count: number;
  low_risk_count: number;
  report_url: string;
}

export interface AIAnalysis {
  risk_level: RiskLevel;
  risk_score: number;
  contract_element: string;
  change_summary: string;
  risk_explanation: string;
  review_suggestion: string;
}

export interface ReportAIAnalysis {
  risk_level: RiskLevel;
  summary: string;
  major_risks: string[];
  review_suggestions: string[];
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
  ai_analysis: AIAnalysis | null;
  original_screenshot: string;
  compare_screenshot: string;
  original_screenshot_url?: string;
  compare_screenshot_url?: string;
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
