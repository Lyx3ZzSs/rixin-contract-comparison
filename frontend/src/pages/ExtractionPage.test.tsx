import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExtractionPage } from "./ExtractionPage";

const getViewport = vi.fn((options: { scale: number }) => ({
  width: 420 * options.scale,
  height: 600 * options.scale,
}));

vi.mock("pdfjs-dist", () => ({
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

vi.mock("pdfjs-dist/build/pdf.worker.mjs?url", () => ({
  default: "pdf-worker-test-url",
}));

describe("ExtractionPage PDF preview", () => {
  beforeEach(() => {
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
    await user.click(screen.getAllByRole("button", { name: "编辑" })[0]);
    await user.clear(screen.getByLabelText("编辑字段名称"));
    await user.type(screen.getByLabelText("编辑字段名称"), "采购方名称");
    await user.clear(screen.getByLabelText("编辑字段描述"));
    await user.type(screen.getByLabelText("编辑字段描述"), "采购方主体名称");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(screen.getByText("采购方名称")).toBeInTheDocument();
    expect(screen.getByText("采购方主体名称")).toBeInTheDocument();
    expect(screen.queryByLabelText("编辑字段名称")).not.toBeInTheDocument();
  });
});
