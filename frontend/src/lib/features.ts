// Feature flags controlled via Vite environment variables.
// These are statically replaced at build time; restart dev server after changing .env.

/** Whether the contract intelligent extraction (合同智能提取) menu and pages are enabled. */
export const isExtractionEnabled: boolean =
  import.meta.env.VITE_ENABLE_EXTRACTION !== "false";
