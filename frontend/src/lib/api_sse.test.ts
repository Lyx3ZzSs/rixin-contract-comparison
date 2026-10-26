import { waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { authorizedFetch } = vi.hoisted(() => ({ authorizedFetch: vi.fn() }));

vi.mock("./authFetch", () => ({ authorizedFetch }));

import { createProgressEventSource } from "./api_sse";

afterEach(() => vi.clearAllMocks());

describe("createProgressEventSource", () => {
  it("parses authenticated SSE data across chunks and ignores keepalives", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(": keepalive\n\ndata: {\"task_id\":\"T1\""));
        controller.enqueue(encoder.encode(",\"status\":\"PROCESSING\"}\n\n"));
        controller.close();
      },
    });
    authorizedFetch.mockResolvedValue(new Response(body, { status: 200 }));
    const stream = createProgressEventSource("T1");
    const messages: string[] = [];
    stream.onmessage = (event) => messages.push(event.data);

    await waitFor(() => expect(messages).toEqual(['{"task_id":"T1","status":"PROCESSING"}']));
    expect(authorizedFetch).toHaveBeenCalledWith(expect.stringContaining("/api/compare/T1/progress"), expect.any(Object));
  });

  it("reports a clean EOF so callers can fall back to polling", async () => {
    authorizedFetch.mockResolvedValue(new Response(new ReadableStream({ start(controller) { controller.close(); } }), { status: 200 }));
    const stream = createProgressEventSource("T-EOF");
    const onerror = vi.fn();
    stream.onerror = onerror;

    await waitFor(() => expect(onerror).toHaveBeenCalledTimes(1));
    expect(onerror.mock.calls[0]?.[0]).toBeInstanceOf(Error);
  });

  it("aborts the authenticated request and suppresses fallback after close", async () => {
    let requestSignal: AbortSignal | undefined;
    authorizedFetch.mockImplementation(async (_url: string, init?: RequestInit) => {
      requestSignal = init?.signal ?? undefined;
      return await new Promise<Response>(() => undefined);
    });
    const stream = createProgressEventSource("T-CLOSE");
    const onerror = vi.fn();
    stream.onerror = onerror;

    stream.close();

    expect(requestSignal?.aborted).toBe(true);
    await Promise.resolve();
    expect(onerror).not.toHaveBeenCalled();
  });
});
