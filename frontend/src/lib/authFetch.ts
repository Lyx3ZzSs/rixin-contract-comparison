import { currentInternalPath, REAUTH_ATTEMPT_KEY } from "../auth/config";
import { getUserManager } from "../auth/userManager";

const statusMessages: Record<number, string> = {
  403: "当前用户没有执行此操作的权限。",
  404: "任务不存在或无权访问。",
  503: "统一身份认证服务暂时不可用。",
};

let reauthenticationPromise: Promise<void> | undefined;

export class ApiError extends Error {
  constructor(readonly status: number, message = statusMessages[status] ?? `请求失败 (${status})`) {
    super(message);
    this.name = "ApiError";
  }
}

export async function authorizedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const manager = getUserManager();
  let user = await manager.getUser();
  if (!user?.access_token || user.expired) {
    try {
      user = await manager.signinSilent();
    } catch {
      await beginReauthentication();
      throw new ApiError(401, "统一身份认证会话已失效，请重新登录。");
    }
  }
  if (!user?.access_token || user.expired) {
    await beginReauthentication();
    throw new ApiError(401, "统一身份认证会话已失效，请重新登录。");
  }

  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${user.access_token}`);
  const response = await fetch(input, { ...init, headers });
  if (response.status === 401) {
    await beginReauthentication();
    throw new ApiError(401, "统一身份认证会话已失效，请重新登录。");
  }
  return response;
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
