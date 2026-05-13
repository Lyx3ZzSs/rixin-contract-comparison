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
}

export interface AIAnalysis {
  risk_level: RiskLevel;
  risk_score: number;
  contract_element: string;
  change_summary: string;
  risk_explanation: string;
  review_suggestion: string;
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
}
