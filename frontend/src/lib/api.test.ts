import { describe, expect, it, vi } from "vitest";

import { compareContracts, extractFields, getApiBaseUrl, toApiUrl } from "./api";

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

  it("sends extraction fields with the compatibility text type and semantic flag", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ results: [], errors: [] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await extractFields(new File(["pdf"], "contract.pdf", { type: "application/pdf" }), [
      {
        id: "contract-amount",
        name: "合同金额",
        type: "金额",
        description: "合同总金额",
        semanticExtraction: false,
      },
    ]);

    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    const fields = JSON.parse(String(body.get("fields"))) as Array<{ type: string; semantic_extraction: boolean }>;
    expect(fields[0].type).toBe("文本");
    expect(fields[0].semantic_extraction).toBe(false);

    vi.unstubAllGlobals();
  });
});
