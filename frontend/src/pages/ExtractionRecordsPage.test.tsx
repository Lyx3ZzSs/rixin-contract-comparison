import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getExtractionRecords, getExtractionTask } from "../lib/api";
import { ExtractionRecordsPage } from "./ExtractionRecordsPage";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    getExtractionRecords: vi.fn(),
    getExtractionTask: vi.fn(),
  };
});

const mockedGetExtractionRecords = vi.mocked(getExtractionRecords);
const mockedGetExtractionTask = vi.mocked(getExtractionTask);

describe("ExtractionRecordsPage", () => {
  beforeEach(() => {
    mockedGetExtractionRecords.mockReset();
    mockedGetExtractionTask.mockReset();
  });

  it("renders extraction records and opens a detail panel", async () => {
    const user = userEvent.setup();
    mockedGetExtractionRecords.mockResolvedValue([
      {
        task_id: "E001",
        task_type: "extraction",
        status: "COMPLETED",
        created_at: "2026-05-21T09:00:00+00:00",
        updated_at: "2026-05-21T09:05:00+00:00",
        filename: "合同.pdf",
        file_url: "/api/extract/E001/file",
        extractor_used: "ppocrv5_llm",
        field_count: 2,
        found_count: 1,
        not_found_count: 1,
        error_count: 0,
      },
    ]);
    mockedGetExtractionTask.mockResolvedValue({
      task_id: "E001",
      task_type: "extraction",
      status: "COMPLETED",
      stage: "",
      filename: "合同.pdf",
      file_url: "/api/extract/E001/file",
      extractor_used: "ppocrv5_llm",
      fields: [
        { id: "party-a-name", name: "甲方名称", type: "金额", description: "甲方名称", semantic_extraction: true },
      ],
      results: [
        {
          field_id: "party-a-name",
          field_name: "甲方名称",
          value: "日新公司",
          confidence: 0.92,
          source_snippet: "甲方：日新公司",
          status: "found",
          extraction_method: "semantic",
        },
      ],
      errors: [],
    });

    render(<ExtractionRecordsPage onCreateExtraction={vi.fn()} />);

    expect(await screen.findByText("合同.pdf")).toBeInTheDocument();
    expect(screen.getByLabelText("合同提取记录列表")).toHaveTextContent("ppocrv5_llm");
    await user.click(screen.getByRole("button", { name: "查看详情" }));

    await waitFor(() => expect(mockedGetExtractionTask).toHaveBeenCalledWith("E001"));
    expect(screen.getByLabelText("提取详情")).toHaveTextContent("日新公司");
    expect(screen.getByLabelText("提取详情")).toHaveTextContent("甲方名称");
    expect(screen.getByLabelText("提取详情")).toHaveTextContent("语义提取");
    expect(screen.getByLabelText("提取详情")).not.toHaveTextContent("金额");
    expect(screen.getByRole("link", { name: "打开原文件" })).toHaveAttribute(
      "href",
      "http://127.0.0.1:8000/api/extract/E001/file",
    );
  });

  it("shows an empty state", async () => {
    mockedGetExtractionRecords.mockResolvedValue([]);

    render(<ExtractionRecordsPage onCreateExtraction={vi.fn()} />);

    expect(await screen.findByText("暂无提取记录")).toBeInTheDocument();
  });

  it("shows a loading error", async () => {
    mockedGetExtractionRecords.mockRejectedValue(new Error("network down"));

    render(<ExtractionRecordsPage onCreateExtraction={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("network down");
  });
});
