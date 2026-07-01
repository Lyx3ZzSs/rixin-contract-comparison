import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createQualityExpectedDiff,
  deleteQualityExpectedDiff,
  evaluateQuality,
  exportQualityCase,
  getQualityCase,
  listQualityCases,
  runQualityRegression,
  toApiUrl,
  updateQualityExpectedDiff,
} from "./api";

function mockJsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), { status: 200 });
}

describe("quality api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads quality cases from the quality cases endpoint", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => mockJsonResponse({ cases: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listQualityCases()).resolves.toEqual({ cases: [] });

    expect(fetchMock).toHaveBeenCalledWith(toApiUrl("/api/quality/cases"));
  });

  it("updates an expected diff with encoded case id, patch method, and JSON body", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        mockJsonResponse({
          summary: { case_id: "case/with space" },
          expected: { expected_diffs: [] },
          actual_diffs: [],
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const payload = {
      review_status: "APPROVED" as const,
      notes: "verified",
    };

    await updateQualityExpectedDiff("case/with space", 2, payload);

    expect(fetchMock).toHaveBeenCalledWith(
      toApiUrl("/api/quality/cases/case%2Fwith%20space/expected-diffs/2"),
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  });

  it("exports a quality case from a task id", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        mockJsonResponse({
          case_id: "case-1",
          task_id: "task-1",
          expected_diff_count: 2,
          actual_diff_count: 2,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const payload = { task_id: "task-1", case_id: "case-1", force: true };

    await expect(exportQualityCase(payload)).resolves.toEqual({
      case_id: "case-1",
      task_id: "task-1",
      expected_diff_count: 2,
      actual_diff_count: 2,
    });
    expect(fetchMock).toHaveBeenCalledWith(toApiUrl("/api/quality/cases/export"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  });

  it("creates expected diffs and runs quality jobs on the correct endpoints", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        mockJsonResponse({
          run_id: "run-1",
          status: "passed",
          report: {},
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createQualityExpectedDiff("case-1", { title_contains: "Amount" });
    await evaluateQuality({ dataset_splits: ["dev"], run_id: "eval-1" });
    await runQualityRegression({ dataset_splits: ["regression"], baseline_name: "current", run_id: "reg-1" });

    expect(fetchMock).toHaveBeenNthCalledWith(1, toApiUrl("/api/quality/cases/case-1/expected-diffs"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title_contains: "Amount" }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, toApiUrl("/api/quality/evaluate"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_splits: ["dev"], run_id: "eval-1" }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, toApiUrl("/api/quality/regression"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_splits: ["regression"], baseline_name: "current", run_id: "reg-1" }),
    });
  });

  it("loads a quality case with an encoded case id", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        mockJsonResponse({
          summary: { case_id: "case/with space" },
          expected: {},
          actual_diffs: [],
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getQualityCase("case/with space");

    expect(fetchMock).toHaveBeenCalledWith(toApiUrl("/api/quality/cases/case%2Fwith%20space"));
  });

  it("deletes expected diffs from the encoded quality case endpoint", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        mockJsonResponse({
          summary: { case_id: "case/with space" },
          expected: {},
          actual_diffs: [],
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await deleteQualityExpectedDiff("case/with space", 1);

    expect(fetchMock).toHaveBeenCalledWith(
      toApiUrl("/api/quality/cases/case%2Fwith%20space/expected-diffs/1"),
      { method: "DELETE" },
    );
  });
});
