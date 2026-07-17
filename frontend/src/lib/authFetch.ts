import { currentInternalPath, REAUTH_ATTEMPT_KEY } from "../auth/config";
import { getUserManager } from "../auth/userManager";

const statusMessages: Record<number, string> = {
  403: "当前用户没有执行此操作的权限。",
  404: "任务不存在或无权访问。",
  503: "统一身份认证服务暂时不可用。",
};

let reauthenticationPromise: Promise<void> | undefined;

export interface AuthorizedFetchOptions {
  timeoutMs?: number;
}

export class ApiError extends Error {
  constructor(readonly status: number, message = statusMessages[status] ?? `请求失败 (${status})`) {
    super(message);
    this.name = "ApiError";
  }
}

export async function authorizedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
  options?: AuthorizedFetchOptions,
): Promise<Response> {
  const cancellation = mergeAbortSignals(init?.signal ?? undefined, options?.timeoutMs);
  try {
    throwIfAborted(cancellation.signal);
    const manager = getUserManager();
    let user = await waitForAbortable(manager.getUser(), cancellation.signal);
    throwIfAborted(cancellation.signal);
    if (!user?.access_token || user.expired) {
      try {
        user = await waitForAbortable(manager.signinSilent(), cancellation.signal);
      } catch {
        throwIfAborted(cancellation.signal);
        await beginReauthentication();
        throw new ApiError(401, "统一身份认证会话已失效，请重新登录。");
      }
    }
    throwIfAborted(cancellation.signal);
    if (!user?.access_token || user.expired) {
      await beginReauthentication();
      throw new ApiError(401, "统一身份认证会话已失效，请重新登录。");
    }

    const headers = new Headers(init?.headers);
    headers.set("Authorization", `Bearer ${user.access_token}`);
    const response = await fetch(input, { ...init, headers, signal: cancellation.signal });
    if (response.status === 401) {
      await beginReauthentication();
      throw new ApiError(401, "统一身份认证会话已失效，请重新登录。");
    }
    return response;
  } finally {
    cancellation.cleanup();
  }
}

export async function downloadAuthenticatedFile(url: string, filename: string): Promise<void> {
  const response = await authorizedFetch(url);
  if (!response.ok) throw new ApiError(response.status);
  const objectUrl = URL.createObjectURL(await response.blob());
  try {
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

async function beginReauthentication(): Promise<void> {
  if (!reauthenticationPromise) {
    const attempts = Number(window.sessionStorage.getItem(REAUTH_ATTEMPT_KEY) ?? "0");
    if (attempts >= 1) return;
    window.sessionStorage.setItem(REAUTH_ATTEMPT_KEY, String(attempts + 1));
    const manager = getUserManager();
    reauthenticationPromise = Promise.resolve()
      .then(() => manager.removeUser())
      .then(() => manager.signinRedirect({ state: { returnTo: currentInternalPath() } }))
      .finally(() => {
        reauthenticationPromise = undefined;
      });
  }
  await reauthenticationPromise;
}

function mergeAbortSignals(callerSignal: AbortSignal | undefined, timeoutMs: number | undefined): {
  signal: AbortSignal | undefined;
  cleanup: () => void;
} {
  if (timeoutMs === undefined) {
    return { signal: callerSignal, cleanup: () => undefined };
  }

  const controller = new AbortController();
  const abort = (reason: unknown) => {
    if (!controller.signal.aborted) controller.abort(reason);
  };
  const abortFromCaller = () => abort(callerSignal?.reason);
  if (callerSignal?.aborted) {
    abortFromCaller();
  } else {
    callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }
  const timeout = window.setTimeout(() => abort(new DOMException("Request timed out", "AbortError")), timeoutMs);

  return {
    signal: controller.signal,
    cleanup: () => {
      window.clearTimeout(timeout);
      callerSignal?.removeEventListener("abort", abortFromCaller);
    },
  };
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (!signal?.aborted) return;
  throw signal.reason ?? new DOMException("Request cancelled", "AbortError");
}

function waitForAbortable<T>(promise: Promise<T>, signal: AbortSignal | undefined): Promise<T> {
  if (!signal) return promise;
  throwIfAborted(signal);

  return new Promise<T>((resolve, reject) => {
    const abort = () => {
      cleanup();
      reject(signal.reason ?? new DOMException("Request cancelled", "AbortError"));
    };
    const cleanup = () => signal.removeEventListener("abort", abort);

    signal.addEventListener("abort", abort, { once: true });
    promise.then(
      (value) => {
        cleanup();
        resolve(value);
      },
      (error: unknown) => {
        cleanup();
        reject(error);
      },
    );
  });
}
