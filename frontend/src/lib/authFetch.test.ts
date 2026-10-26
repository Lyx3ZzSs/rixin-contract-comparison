import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, authorizedFetch, downloadAuthenticatedFile } from "./authFetch";

const getUser = vi.fn();
const signinSilent = vi.fn();
const removeUser = vi.fn().mockResolvedValue(undefined);
const signinRedirect = vi.fn().mockResolvedValue(undefined);

function captureError(promise: Promise<unknown>): { error: unknown } {
  const result: { error: unknown } = { error: undefined };
  void promise.catch((reason: unknown) => { result.error = reason; });
  return result;
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

vi.mock("../auth/userManager", () => ({
  getUserManager: () => ({ getUser, signinSilent, removeUser, signinRedirect }),
}));

afterEach(() => {
  getUser.mockReset();
  signinSilent.mockReset();
  removeUser.mockClear();
  signinRedirect.mockClear();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  vi.useRealTimers();
});

describe("authorizedFetch", () => {
  it("does not resolve OIDC state or add a bearer token in disabled mode", async () => {
    vi.stubEnv("VITE_AUTH_MODE", "disabled");
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await authorizedFetch("/api/tasks", { headers: { "X-Request-ID": "request-id" } });

    expect(getUser).not.toHaveBeenCalled();
    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get("Authorization")).toBeNull();
    expect(headers.get("X-Request-ID")).toBe("request-id");
  });

  it("adds the current access token while preserving request headers", async () => {
    getUser.mockResolvedValue({ access_token: "access-token", expired: false });
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await authorizedFetch("/api/tasks", { headers: { "X-Request-ID": "request-id" } });

    expect(fetchMock).toHaveBeenCalledWith("/api/tasks", expect.objectContaining({ headers: expect.any(Headers) }));
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer access-token");
    expect(headers.get("X-Request-ID")).toBe("request-id");
  });

  it("aborts an authorized request when its timeout expires", async () => {
    vi.useFakeTimers();
    getUser.mockResolvedValue({ access_token: "access-token", expired: false });
    let requestSignal: AbortSignal | undefined;
    let notifyFetchStarted!: () => void;
    const fetchStarted = new Promise<void>((resolve) => { notifyFetchStarted = resolve; });
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      requestSignal = init?.signal ?? undefined;
      notifyFetchStarted();
      return new Promise<Response>((_resolve, reject) => {
        requestSignal?.addEventListener("abort", () => reject(requestSignal?.reason), { once: true });
      });
    }));

    const request = authorizedFetch("/api/tasks", undefined, { timeoutMs: 1000 });
    await fetchStarted;
    vi.advanceTimersByTime(1000);

    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(requestSignal?.aborted).toBe(true);
  });

  it("propagates a caller abort signal to the authorized fetch", async () => {
    getUser.mockResolvedValue({ access_token: "access-token", expired: false });
    const caller = new AbortController();
    let requestSignal: AbortSignal | undefined;
    let notifyFetchStarted!: () => void;
    const fetchStarted = new Promise<void>((resolve) => { notifyFetchStarted = resolve; });
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      requestSignal = init?.signal ?? undefined;
      notifyFetchStarted();
      return new Promise<Response>((_resolve, reject) => {
        requestSignal?.addEventListener("abort", () => reject(requestSignal?.reason), { once: true });
      });
    }));

    const request = authorizedFetch("/api/tasks", { signal: caller.signal }, { timeoutMs: 1000 });
    await fetchStarted;
    caller.abort(new DOMException("Request cancelled", "AbortError"));

    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(requestSignal).not.toBe(caller.signal);
    expect(requestSignal?.aborted).toBe(true);
  });

  it("uses a merged signal when the timeout cancels a request with a caller signal", async () => {
    vi.useFakeTimers();
    getUser.mockResolvedValue({ access_token: "access-token", expired: false });
    const caller = new AbortController();
    let requestSignal: AbortSignal | undefined;
    let abortCount = 0;
    let notifyFetchStarted!: () => void;
    const fetchStarted = new Promise<void>((resolve) => { notifyFetchStarted = resolve; });
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      requestSignal = init?.signal ?? undefined;
      notifyFetchStarted();
      return new Promise<Response>((_resolve, reject) => {
        requestSignal?.addEventListener("abort", () => {
          abortCount += 1;
          reject(requestSignal?.reason);
        }, { once: true });
      });
    }));

    const request = authorizedFetch("/api/tasks", { signal: caller.signal }, { timeoutMs: 1000 });
    await fetchStarted;
    vi.advanceTimersByTime(1000);

    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(requestSignal?.aborted).toBe(true);
    expect(caller.signal.aborted).toBe(false);
    expect(abortCount).toBe(1);
  });

  it("cancels a pending user lookup when the caller aborts", async () => {
    vi.useFakeTimers();
    getUser.mockImplementation(() => new Promise(() => undefined));
    const caller = new AbortController();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const request = authorizedFetch("/api/tasks", { signal: caller.signal }, { timeoutMs: 1000 });
    const result = captureError(request);
    caller.abort(new DOMException("Request cancelled", "AbortError"));
    await flushPromises();

    expect(result.error).toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(removeUser).not.toHaveBeenCalled();
    expect(signinRedirect).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cancels a pending user lookup when the read timeout expires", async () => {
    vi.useFakeTimers();
    getUser.mockImplementation(() => new Promise(() => undefined));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const request = authorizedFetch("/api/tasks", undefined, { timeoutMs: 1000 });
    const result = captureError(request);
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();

    expect(result.error).toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(removeUser).not.toHaveBeenCalled();
    expect(signinRedirect).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cancels a pending silent sign-in when the caller aborts", async () => {
    vi.useFakeTimers();
    getUser.mockResolvedValue({ access_token: "expired-token", expired: true });
    signinSilent.mockImplementation(() => new Promise(() => undefined));
    const caller = new AbortController();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const request = authorizedFetch("/api/tasks", { signal: caller.signal }, { timeoutMs: 1000 });
    const result = captureError(request);
    await vi.advanceTimersByTimeAsync(0);
    caller.abort(new DOMException("Request cancelled", "AbortError"));
    await flushPromises();

    expect(result.error).toMatchObject({ name: "AbortError" });
    expect(signinSilent).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(removeUser).not.toHaveBeenCalled();
    expect(signinRedirect).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cancels a pending silent sign-in when the read timeout expires", async () => {
    vi.useFakeTimers();
    getUser.mockResolvedValue({ access_token: "expired-token", expired: true });
    signinSilent.mockImplementation(() => new Promise(() => undefined));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const request = authorizedFetch("/api/tasks", undefined, { timeoutMs: 1000 });
    const result = captureError(request);
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();

    expect(result.error).toMatchObject({ name: "AbortError" });
    expect(signinSilent).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(removeUser).not.toHaveBeenCalled();
    expect(signinRedirect).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("propagates a caller cancellation during silent sign-in without reauthenticating", async () => {
    getUser.mockResolvedValue({ access_token: "expired-token", expired: true });
    let rejectSilent!: (reason?: unknown) => void;
    signinSilent.mockImplementation(() => new Promise((_resolve, reject) => { rejectSilent = reject; }));
    const caller = new AbortController();

    const request = authorizedFetch("/api/tasks", { signal: caller.signal });
    await vi.waitFor(() => expect(signinSilent).toHaveBeenCalledTimes(1));
    caller.abort(new DOMException("Request cancelled", "AbortError"));
    rejectSilent(new Error("silent sign-in unavailable"));

    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(removeUser).not.toHaveBeenCalled();
    expect(signinRedirect).not.toHaveBeenCalled();
  });

  it("propagates a timeout during silent sign-in without reauthenticating", async () => {
    vi.useFakeTimers();
    getUser.mockResolvedValue({ access_token: "expired-token", expired: true });
    let rejectSilent!: (reason?: unknown) => void;
    signinSilent.mockImplementation(() => new Promise((_resolve, reject) => { rejectSilent = reject; }));

    const request = authorizedFetch("/api/tasks", undefined, { timeoutMs: 1000 });
    await vi.waitFor(() => expect(signinSilent).toHaveBeenCalledTimes(1));
    vi.advanceTimersByTime(1000);
    rejectSilent(new Error("silent sign-in unavailable"));

    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(removeUser).not.toHaveBeenCalled();
    expect(signinRedirect).not.toHaveBeenCalled();
  });

  it("maps protected API failures to stable user messages", () => {
    expect(new ApiError(403).message).toBe("当前用户没有执行此操作的权限。");
    expect(new ApiError(404).message).toBe("任务不存在或无权访问。");
    expect(new ApiError(503).message).toBe("统一身份认证服务暂时不可用。");
  });

  it("downloads with authenticated fetch and releases its object URL", async () => {
    getUser.mockResolvedValue({ access_token: "access-token", expired: false });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new Blob(["pdf"]), { status: 200 })));
    const createObjectURL = vi.fn().mockReturnValue("blob:report");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    await downloadAuthenticatedFile("/report", "report.pdf");

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:report");
    expect(click).toHaveBeenCalledTimes(1);
  });
});
