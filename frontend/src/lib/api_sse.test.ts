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
});
