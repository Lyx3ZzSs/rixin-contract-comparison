import { describe, expect, it, vi } from "vitest";

import { navigateToTask, readRoute } from "./routes";

describe("routes", () => {
  it("parses known application paths", () => {
    expect(readRoute("/")).toEqual({ name: "home" });
    expect(readRoute("/compare/records")).toEqual({ name: "records" });
    expect(readRoute("/extract")).toEqual({ name: "extract" });
    expect(readRoute("/extract/records")).toEqual({ name: "extractRecords" });
    expect(readRoute("/extract/fields")).toEqual({ name: "extractFields" });
    expect(readRoute("/tasks/task%201")).toEqual({ name: "task", taskId: "task 1" });
  });

  it("updates browser history and emits a popstate event", () => {
    const listener = vi.fn();
    window.addEventListener("popstate", listener);

    navigateToTask("task 1");

    expect(window.location.pathname).toBe("/tasks/task%201");
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener("popstate", listener);
  });
});
