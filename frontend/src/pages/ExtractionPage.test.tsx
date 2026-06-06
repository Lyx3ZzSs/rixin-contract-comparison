import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as pdfjsLib from "pdfjs-dist/legacy/build/pdf.mjs";

import { EXTRACTION_FIELD_LIBRARY_STORAGE_KEY } from "../lib/extractionFieldLibrary";
import { ExtractionPage } from "./ExtractionPage";

const getViewport = vi.fn((options: { scale: number }) => ({
  width: 420 * options.scale,
  height: 600 * options.scale,
}));

vi.mock("pdfjs-dist/legacy/build/pdf.mjs", () => ({
  GlobalWorkerOptions: {},
  getDocument: vi.fn(() => ({
    promise: Promise.resolve({
      numPages: 3,
      destroy: vi.fn(),
      getPage: vi.fn(() =>
        Promise.resolve({
          getViewport,
          render: vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() })),
        }),
      ),
    }),
    destroy: vi.fn(),
  })),
}));

vi.mock("pdfjs-dist/legacy/build/pdf.worker.mjs?url", () => ({
  default: "pdf-worker-test-url",
}));

describe("ExtractionPage PDF preview", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
    getViewport.mockClear();
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
      configurable: true,
      get() {
        return this.classList?.contains("extract-pdf-scroll-shell") ? 600 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
      configurable: true,
      get() {
        return this.dataset?.pageNumber ? 600 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "offsetTop", {
      configurable: true,
      get() {
        const pageNumber = Number(this.dataset?.pageNumber);
        return Number.isFinite(pageNumber) && pageNumber > 0 ? (pageNumber - 1) * 612 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: vi.fn(function scrollTo(this: HTMLElement, options?: ScrollToOptions | number, y?: number) {
        this.scrollTop = typeof options === "number" ? y ?? 0 : options?.top ?? 0;
        fireEvent.scroll(this);
      }),
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: vi.fn(() => ({})),
    });
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:contract-preview"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("renders the PDF at 90% while showing 100% in the toolbar", async () => {
    const user = userEvent.setup();
    render(<ExtractionPage />);

    await user.upload(screen.getByLabelText("上传合同提取文件"), new File(["pdf"], "contract.pdf", { type: "application/pdf" }));

    await waitFor(() => expect(getViewport).toHaveBeenCalledWith({ scale: 0.9 }));
    expect(screen.getByLabelText("当前缩放比例")).toHaveTextContent("100%");
    expect(screen.queryByLabelText("PDF翻页")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("PDF缩放")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    expect(screen.getAllByLabelText(/第 \d 页/)).toHaveLength(3);
  });

  it("shows the classified PDF loading error", async () => {
    const user = userEvent.setup();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    let rejectLoading: (reason: unknown) => void = () => undefined;
    const loadingPromise = new Promise<never>((_, reject) => {
      rejectLoading = reject;
    });
    vi.mocked(pdfjsLib.getDocument).mockReturnValueOnce({
      promise: loadingPromise,
      destroy: vi.fn(),
    } as unknown as ReturnType<typeof pdfjsLib.getDocument>);
    render(<ExtractionPage />);

    await user.upload(
      screen.getByLabelText("上传合同提取文件"),
      new File(["pdf"], "damaged.pdf", { type: "application/pdf" }),
    );
    await act(async () => rejectLoading({ name: "InvalidPDFException" }));

    expect(await screen.findByText("PDF 已损坏或格式无效。")).toBeInTheDocument();
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("scrolls the continuous PDF preview from toolbar page controls", async () => {
    const user = userEvent.setup();
    render(<ExtractionPage />);

    await user.upload(screen.getByLabelText("上传合同提取文件"), new File(["pdf"], "contract.pdf", { type: "application/pdf" }));
    await screen.findByLabelText("合同 PDF 预览");

    await user.click(screen.getByRole("button", { name: "下一页" }));

    await waitFor(() => expect(screen.getByLabelText("当前页码")).toHaveValue(2));
  });

  it("updates the current page from native preview scrolling", async () => {
    const user = userEvent.setup();
    render(<ExtractionPage />);

    await user.upload(screen.getByLabelText("上传合同提取文件"), new File(["pdf"], "contract.pdf", { type: "application/pdf" }));
    const preview = await screen.findByLabelText("合同 PDF 预览");
    const scrollShell = preview.querySelector(".extract-pdf-scroll-shell") as HTMLElement;
    scrollShell.scrollTop = 1224;

    fireEvent.scroll(scrollShell);

    await waitFor(() => expect(screen.getByLabelText("当前页码")).toHaveValue(3));
  });

  it("edits extraction fields inline", async () => {
    const user = userEvent.setup();
    render(<ExtractionPage />);

    await user.upload(screen.getByLabelText("上传合同提取文件"), new File(["pdf"], "contract.pdf", { type: "application/pdf" }));
    expect(screen.queryByText("字段类型")).not.toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "编辑" })[0]);
    expect(screen.queryByLabelText("编辑字段类型")).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText("编辑字段名称"));
    await user.type(screen.getByLabelText("编辑字段名称"), "采购方名称");
    await user.clear(screen.getByLabelText("编辑字段描述"));
    await user.type(screen.getByLabelText("编辑字段描述"), "采购方主体名称");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(screen.getByText("采购方名称")).toBeInTheDocument();
    expect(screen.getByText("采购方主体名称")).toBeInTheDocument();
    expect(screen.queryByLabelText("编辑字段名称")).not.toBeInTheDocument();
  });

  it("uses the local extraction field library as default fields", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      EXTRACTION_FIELD_LIBRARY_STORAGE_KEY,
      JSON.stringify([
        {
          id: "contract-amount",
          name: "合同金额",
          type: "金额",
          description: "合同总金额",
          semanticExtraction: true,
        },
        {
          id: "party-a-name",
          name: "甲方名称",
          type: "文本",
          description: "甲方名称",
          semanticExtraction: false,
        },
      ]),
    );
    render(<ExtractionPage />);

    await user.upload(screen.getByLabelText("上传合同提取文件"), new File(["pdf"], "contract.pdf", { type: "application/pdf" }));

    expect(screen.getByLabelText("提取字段列表")).toHaveTextContent("合同金额");
    expect(screen.getByLabelText("提取字段列表")).toHaveTextContent("甲方名称");
    expect(screen.getByRole("button", { name: "甲方名称语义提取" })).toHaveAttribute("aria-pressed", "false");
  });

  it("submits disabled semantic extraction as strict matching and displays the result method", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      EXTRACTION_FIELD_LIBRARY_STORAGE_KEY,
      JSON.stringify([
        {
          id: "contract-amount",
          name: "合同金额",
          type: "金额",
          description: "合同总金额",
          semanticExtraction: true,
        },
      ]),
    );
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        new Response(
          JSON.stringify({
            task_id: "E001",
            task_type: "extraction",
            status: "COMPLETED",
            stage: "",
            filename: "contract.pdf",
            file_url: "/api/extract/E001/file",
            extractor_used: "ppocrv5_llm",
            fields: [
              { id: "contract-amount", name: "合同金额", type: "金额", description: "合同总金额", semantic_extraction: false },
            ],
            results: [
              {
                field_id: "contract-amount",
                field_name: "合同金额",
                value: "人民币100万元",
                confidence: 1,
                source_snippet: "合同金额：人民币100万元",
                status: "found",
                extraction_method: "explicit",
              },
            ],
            errors: [],
          }),
          { status: 200 },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<ExtractionPage />);

    await user.upload(screen.getByLabelText("上传合同提取文件"), new File(["pdf"], "contract.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "合同金额语义提取" }));
    await user.click(screen.getByRole("button", { name: "开始提取" }));

    await screen.findByText("人民币100万元");
    expect(screen.getByText("严格匹配")).toBeInTheDocument();
    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    const fields = JSON.parse(String(body.get("fields"))) as Array<{ semantic_extraction: boolean }>;
    expect(fields[0].semantic_extraction).toBe(false);
  });

  it("expands and collapses long extraction values", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      EXTRACTION_FIELD_LIBRARY_STORAGE_KEY,
      JSON.stringify([
        {
          id: "payment-terms",
          name: "付款条款",
          type: "文本",
          description: "付款条款",
          semanticExtraction: true,
        },
      ]),
    );
    const longValue =
      "合同签订后十个工作日内支付合同总金额的百分之三十，设备到货并完成初验后支付百分之六十，剩余百分之十作为质保金在质保期满后一次性无息支付。若验收过程中发现质量问题，乙方应在收到通知后五个工作日内完成整改并重新提交验收。";
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        new Response(
          JSON.stringify({
            task_id: "E002",
            task_type: "extraction",
            status: "COMPLETED",
            stage: "",
            filename: "contract.pdf",
            file_url: "/api/extract/E002/file",
            extractor_used: "ppocrv5_llm",
            fields: [
              { id: "payment-terms", name: "付款条款", type: "文本", description: "付款条款", semantic_extraction: true },
            ],
            results: [
              {
                field_id: "payment-terms",
                field_name: "付款条款",
                value: longValue,
                confidence: 0.93,
                source_snippet: longValue,
                status: "found",
                extraction_method: "semantic",
              },
            ],
            errors: [],
          }),
          { status: 200 },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<ExtractionPage />);

    await user.upload(screen.getByLabelText("上传合同提取文件"), new File(["pdf"], "contract.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "开始提取" }));

    await screen.findByText(longValue);
    const expandButton = screen.getByRole("button", { name: "展开" });
    expect(expandButton).toHaveAttribute("aria-expanded", "false");

    await user.click(expandButton);
    expect(screen.getByRole("button", { name: "收起" })).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByRole("button", { name: "收起" }));
    expect(screen.getByRole("button", { name: "展开" })).toHaveAttribute("aria-expanded", "false");
  });

  it("converts Word files to PDF previews", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(new Blob(["%PDF-1.4"], { type: "application/pdf" })));
    vi.stubGlobal("fetch", fetchMock);
    render(<ExtractionPage />);

    await user.upload(
      screen.getByLabelText("上传合同提取文件"),
      new File(["word"], "contract.docx", {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/extract/preview", expect.any(Object)));
    await waitFor(() => expect(getViewport).toHaveBeenCalledWith({ scale: 0.9 }));
    expect(screen.getByLabelText("合同 PDF 预览")).toBeInTheDocument();
  });

  it("shows a Word preview conversion error", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ detail: "预览失败: 未安装 LibreOffice" }), { status: 500 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<ExtractionPage />);

    await user.upload(
      screen.getByLabelText("上传合同提取文件"),
      new File(["word"], "contract.docx", {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      }),
    );

    expect(await screen.findByText("预览失败: 未安装 LibreOffice")).toBeInTheDocument();
  });
});
