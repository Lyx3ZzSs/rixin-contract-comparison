import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./authFetch", () => ({
  ApiError: class ApiError extends Error {
    constructor(readonly status: number, message = `请求失败 (${status})`) { super(message); }
  },
  authorizedFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

import { compareContracts, getApiBaseUrl, getCompareRecords, toApiUrl } from "./api";
import type { AuditItem } from "../types";

const auditStructuralContract: Pick<AuditItem, "structural_flags"> = { structural_flags: [] };

describe("api client URLs", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("keeps backend structural flags on the audit item contract", () => {
    expect(auditStructuralContract.structural_flags).toEqual([]);
  });

  it("uses the configured API base URL without duplicate slashes", () => {
    vi.stubEnv("VITE_API_BASE_PATH", "");
    vi.stubEnv("VITE_BASE_PATH", "");
    vi.stubEnv("VITE_API_BASE_URL", "http://localhost:9000/");

    expect(getApiBaseUrl()).toBe("http://localhost:9000");
    expect(toApiUrl("/api/compare/task-1/report")).toBe("http://localhost:9000/api/compare/task-1/report");
    expect(toApiUrl("api/compare")).toBe("http://localhost:9000/api/compare");
  });

  it("keeps absolute URLs unchanged", () => {
    expect(toApiUrl("https://example.test/file.pdf")).toBe("https://example.test/file.pdf");
  });

  it("uses the configured API base URL before the API base path for local development", () => {
    vi.stubEnv("VITE_BASE_PATH", "/contract");
    vi.stubEnv("VITE_API_BASE_PATH", "/contract/api");
    vi.stubEnv("VITE_API_BASE_URL", "http://127.0.0.1:8000");

    expect(getApiBaseUrl()).toBe("http://127.0.0.1:8000");
    expect(toApiUrl("/api/compare/records")).toBe("http://127.0.0.1:8000/api/compare/records");
    expect(toApiUrl("api/compare/task-1/report")).toBe("http://127.0.0.1:8000/api/compare/task-1/report");
  });

  it("defaults API calls to the app base path when no API override is configured", () => {
    vi.stubEnv("VITE_BASE_PATH", "/contract");
    vi.stubEnv("VITE_API_BASE_PATH", "");
    vi.stubEnv("VITE_API_BASE_URL", "");

    expect(getApiBaseUrl()).toBe("/contract/api");
    expect(toApiUrl("/api/compare/records")).toBe("/contract/api/compare/records");
  });

  it("sends comparison files without exclusion options", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ task_id: "task-1", status: "PROCESSING" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await compareContracts(
      new File(["original"], "original.pdf", { type: "application/pdf" }),
      new File(["compare"], "compare.pdf", { type: "application/pdf" }),
    );

    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.get("original_file")).toBeInstanceOf(File);
    expect(body.get("compare_file")).toBeInstanceOf(File);
    expect(body.has("ignore_punctuation")).toBe(false);
    expect(body.has("ignore_headers_footers")).toBe(false);
    expect(body.has("ignore_stamps")).toBe(false);
    expect(body.has("signing_region_mode")).toBe(false);
  });

  it("sends the stamp exclusion option only when enabled", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ task_id: "task-1", status: "PROCESSING" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await compareContracts(
      new File(["original"], "original.pdf", { type: "application/pdf" }),
      new File(["compare"], "compare.pdf", { type: "application/pdf" }),
      { ignoreStamps: true, ignoreHeadersFooters: false },
    );

    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.get("ignore_stamps")).toBe("true");
    expect(body.has("ignore_punctuation")).toBe(false);
    expect(body.has("ignore_headers_footers")).toBe(false);
  });

  it("sends the signing region mode when provided", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ task_id: "task-1", status: "PROCESSING" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await compareContracts(
      new File(["original"], "original.pdf", { type: "application/pdf" }),
      new File(["compare"], "compare.pdf", { type: "application/pdf" }),
      { ignoreStamps: false, ignoreHeadersFooters: false, signingRegionMode: "off" },
    );

    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.get("signing_region_mode")).toBe("off");
    expect(body.has("ignore_stamps")).toBe(false);
    expect(body.has("ignore_headers_footers")).toBe(false);

    vi.unstubAllGlobals();
  });

  it("sends the full signing region mode when provided", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ task_id: "task-1", status: "PROCESSING" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await compareContracts(
      new File(["original"], "original.pdf", { type: "application/pdf" }),
      new File(["compare"], "compare.pdf", { type: "application/pdf" }),
      { ignoreStamps: false, ignoreHeadersFooters: false, signingRegionMode: "full" },
    );

    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.get("signing_region_mode")).toBe("full");

    vi.unstubAllGlobals();
  });

  it("sends the header footer exclusion option only when enabled", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ task_id: "task-1", status: "PROCESSING" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await compareContracts(
      new File(["original"], "original.pdf", { type: "application/pdf" }),
      new File(["compare"], "compare.pdf", { type: "application/pdf" }),
      { ignoreStamps: false, ignoreHeadersFooters: true },
    );

    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.get("ignore_headers_footers")).toBe("true");
    expect(body.has("ignore_punctuation")).toBe(false);
    expect(body.has("ignore_stamps")).toBe(false);
  });

  it("loads comparison records with pagination and date filters", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        new Response(
          JSON.stringify({
            records: [],
            total: 12,
            page: 2,
            page_size: 10,
            total_pages: 2,
          }),
          { status: 200 },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const payload = await getCompareRecords({
      page: 2,
      pageSize: 10,
      startDate: "2026-05-21",
      endDate: "2026-05-22",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      toApiUrl("/api/compare/records?page=2&page_size=10&start_date=2026-05-21&end_date=2026-05-22"),
      undefined,
    );
    expect(payload.total).toBe(12);
    expect(payload.total_pages).toBe(2);
  });

});
