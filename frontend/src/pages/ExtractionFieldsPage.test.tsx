import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { EXTRACTION_FIELD_LIBRARY_STORAGE_KEY } from "../lib/extractionFieldLibrary";
import { ExtractionFieldsPage } from "./ExtractionFieldsPage";

describe("ExtractionFieldsPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("adds fields and persists the field library", async () => {
    const user = userEvent.setup();
    render(<ExtractionFieldsPage />);

    expect(screen.queryByText("字段类型")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("按字段类型筛选")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "新增字段" }));
    const editor = screen.getByLabelText("新增提取字段");
    expect(within(editor).queryByLabelText("字段类型")).not.toBeInTheDocument();
    await user.type(within(editor).getByLabelText("字段名称"), "合同金额");
    await user.clear(within(editor).getByLabelText("字段描述"));
    await user.type(within(editor).getByLabelText("字段描述"), "合同总金额");
    await user.click(within(editor).getByRole("button", { name: "保存" }));

    expect(screen.getByText("合同金额")).toBeInTheDocument();
    const stored = JSON.parse(window.localStorage.getItem(EXTRACTION_FIELD_LIBRARY_STORAGE_KEY) || "[]") as Array<{
      name: string;
      description: string;
    }>;
    expect(stored).toEqual(expect.arrayContaining([expect.objectContaining({ name: "合同金额", description: "合同总金额" })]));
  });

  it("edits, filters, toggles, deletes, and restores fields", async () => {
    const user = userEvent.setup();
    render(<ExtractionFieldsPage />);

    await user.click(screen.getAllByRole("button", { name: "编辑" })[0]);
    const editor = screen.getByLabelText(/编辑/);
    expect(within(editor).queryByLabelText("字段类型")).not.toBeInTheDocument();
    await user.clear(within(editor).getByLabelText("字段名称"));
    await user.type(within(editor).getByLabelText("字段名称"), "采购方名称");
    await user.click(within(editor).getByRole("button", { name: "保存" }));
    expect(screen.getByText("采购方名称")).toBeInTheDocument();

    await user.type(screen.getByLabelText("搜索提取字段"), "采购方");
    expect(screen.getByLabelText("提取字段库")).toHaveTextContent("采购方名称");
    expect(screen.getByLabelText("提取字段库")).not.toHaveTextContent("乙方名称");

    expect(screen.getByText("默认字段")).toBeInTheDocument();
    expect(screen.getByText("是否默认")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "采购方名称是否默认" }));
    expect(screen.getByRole("button", { name: "采购方名称是否默认" })).toHaveAttribute("aria-pressed", "false");

    await user.click(screen.getByRole("button", { name: /删除/ }));
    expect(screen.getByText("没有匹配字段")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "恢复默认字段" }));
    expect(screen.getAllByText("甲方名称").length).toBeGreaterThan(0);
  });
});
