import { describe, expect, it, vi } from "vitest";

import { compareContracts, getApiBaseUrl, getCompareRecords, toApiUrl } from "./api";

describe("api client URLs", () => {
  it("uses the configured API base URL without duplicate slashes", () => {
    vi.stubEnv("VITE_API_BASE_URL", "http://localhost:9000/");

    expect(getApiBaseUrl()).toBe("http://localhost:9000");
    expect(toApiUrl("/api/compare/task-1/report")).toBe("http://localhost:9000/api/compare/task-1/report");
    expect(toApiUrl("api/compare")).toBe("http://localhost:9000/api/compare");

    vi.unstubAllEnvs();
  });

  it("keeps absolute URLs unchanged", () => {
    expect(toApiUrl("https://example.test/file.pdf")).toBe("https://example.test/file.pdf");
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

    vi.unstubAllGlobals();
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

    vi.unstubAllGlobals();
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
    );
    expect(payload.total).toBe(12);
    expect(payload.total_pages).toBe(2);

    vi.unstubAllGlobals();
  });

});
