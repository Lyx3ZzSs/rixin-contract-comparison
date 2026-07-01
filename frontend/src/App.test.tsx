import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { AppProvider } from "./lib/state";

vi.mock("./pages/UploadPage", () => ({
  UploadPage: () => <div>Mock Upload Page</div>,
}));

vi.mock("./pages/ComparisonRecordsPage", () => ({
  ComparisonRecordsPage: () => <div>Mock Records Page</div>,
}));

vi.mock("./pages/ResultPage", () => ({
  ResultPage: () => <div>Mock Result Page</div>,
}));

vi.mock("./pages/QualityWorkbenchPage", () => ({
  QualityWorkbenchPage: () => <div>Mock Quality Workbench</div>,
}));

const authStorageKey = "rixin_contract_auth_user";

function renderAuthenticatedApp(pathname = "/") {
  window.localStorage.setItem(authStorageKey, "tester");
  window.history.pushState({}, "", pathname);
  render(
    <AppProvider>
      <App />
    </AppProvider>,
  );
}

afterEach(() => {
  window.localStorage.clear();
  window.history.pushState({}, "", "/");
  vi.clearAllMocks();
});

describe("App", () => {
  it("renders the quality workbench from its direct route", async () => {
    const user = userEvent.setup();
    renderAuthenticatedApp("/quality/workbench");

    expect(screen.getByText("Mock Quality Workbench")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));

    const qualityEntry = screen.getByRole("button", { name: /质量工作台/ });
    expect(qualityEntry).toHaveClass("active");
    expect(qualityEntry).toHaveAttribute("aria-current", "page");
  });

  it("navigates to the quality workbench from the comparison menu", async () => {
    const user = userEvent.setup();
    renderAuthenticatedApp("/");

    expect(screen.getByText("Mock Upload Page")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    await user.click(screen.getByRole("button", { name: /质量工作台/ }));

    expect(window.location.pathname).toBe("/quality/workbench");
    expect(screen.getByText("Mock Quality Workbench")).toBeInTheDocument();
  });
});
