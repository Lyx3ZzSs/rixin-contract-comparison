import { extractionFields, type ExtractionFieldDefinition } from "../data/extractionFields";

export const EXTRACTION_FIELD_LIBRARY_STORAGE_KEY = "rixin_extraction_field_library";

function normalizeField(raw: unknown): ExtractionFieldDefinition | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const value = raw as Partial<ExtractionFieldDefinition> & { semantic_extraction?: boolean };
  const id = typeof value.id === "string" ? value.id.trim() : "";
  const name = typeof value.name === "string" ? value.name.trim() : "";
  if (!id || !name) {
    return null;
  }
  return {
    id,
    name,
    type: typeof value.type === "string" && value.type.trim() ? value.type.trim() : "文本",
    description: typeof value.description === "string" ? value.description.trim() : "",
    semanticExtraction:
      typeof value.semanticExtraction === "boolean"
        ? value.semanticExtraction
        : typeof value.semantic_extraction === "boolean"
          ? value.semantic_extraction
          : true,
  };
}

function cloneDefaultFields(): ExtractionFieldDefinition[] {
  return extractionFields.map((field) => ({ ...field }));
}

export function readExtractionFieldLibrary(): ExtractionFieldDefinition[] {
  if (typeof window === "undefined") {
    return cloneDefaultFields();
  }
  try {
    const stored = window.localStorage.getItem(EXTRACTION_FIELD_LIBRARY_STORAGE_KEY);
    if (!stored) {
      const defaults = cloneDefaultFields();
      writeExtractionFieldLibrary(defaults);
      return defaults;
    }
    const parsed = JSON.parse(stored) as unknown;
    if (!Array.isArray(parsed)) {
      throw new Error("字段库不是数组");
    }
    const fields = parsed.map(normalizeField).filter((field): field is ExtractionFieldDefinition => Boolean(field));
    if (fields.length === 0) {
      const defaults = cloneDefaultFields();
      writeExtractionFieldLibrary(defaults);
      return defaults;
    }
    return fields;
  } catch {
    const defaults = cloneDefaultFields();
    writeExtractionFieldLibrary(defaults);
    return defaults;
  }
}

export function writeExtractionFieldLibrary(fields: ExtractionFieldDefinition[]): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(EXTRACTION_FIELD_LIBRARY_STORAGE_KEY, JSON.stringify(fields));
}

export function resetExtractionFieldLibrary(): ExtractionFieldDefinition[] {
  const defaults = cloneDefaultFields();
  writeExtractionFieldLibrary(defaults);
  return defaults;
}

export function createExtractionFieldId(name: string): string {
  const normalized = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `field-${normalized || "custom"}-${Date.now().toString(36)}`;
}
