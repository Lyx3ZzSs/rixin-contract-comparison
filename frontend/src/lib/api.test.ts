import { describe, expect, it, vi } from "vitest";

import { compareContracts, getApiBaseUrl, toApiUrl } from "./api";

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

});
