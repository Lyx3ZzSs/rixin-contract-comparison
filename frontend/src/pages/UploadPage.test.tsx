import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { UploadPage } from "./UploadPage";

describe("UploadPage", () => {
  it("renders the contract comparison workspace and keeps upload controls", () => {
    render(<UploadPage onTaskCreated={vi.fn()} />);

    expect(screen.getByText("智能合同对比")).toBeInTheDocument();
    expect(screen.getByLabelText("原版文件")).toBeInTheDocument();
    expect(screen.getByLabelText("新版文件")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始对比" })).toBeDisabled();
  });
});
