import { afterEach, describe, expect, it, vi } from "vitest";

import {
  navigateHome,
  navigateToComparisonRecords,
  navigateToTask,
  readRoute,
} from "./routes";

describe("app routes", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    window.history.replaceState({}, "", "/");
  });

  it("reads routes under the configured app base path", () => {
    vi.stubEnv("VITE_BASE_PATH", "/contract");

    expect(readRoute("/contract/compare/records")).toEqual({ name: "records" });
    expect(readRoute("/contract/tasks/task-1")).toEqual({ name: "task", taskId: "task-1" });
    expect(readRoute("/contract/quality/workbench")).toEqual({ name: "home" });
    expect(readRoute("/contract/")).toEqual({ name: "home" });
  });

  it("navigates within the configured app base path", () => {
    vi.stubEnv("VITE_BASE_PATH", "/contract");

    navigateToComparisonRecords();
    expect(window.location.pathname).toBe("/contract/compare/records");

    navigateToTask("task/1");
    expect(window.location.pathname).toBe("/contract/tasks/task%2F1");

    navigateHome();
    expect(window.location.pathname).toBe("/contract/");
  });
});
