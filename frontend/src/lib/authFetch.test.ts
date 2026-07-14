import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, authorizedFetch, downloadAuthenticatedFile } from "./authFetch";

const getUser = vi.fn();
const signinSilent = vi.fn();
const removeUser = vi.fn().mockResolvedValue(undefined);
const signinRedirect = vi.fn().mockResolvedValue(undefined);

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
});

describe("authorizedFetch", () => {
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
