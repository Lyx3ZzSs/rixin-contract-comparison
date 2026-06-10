import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PdfDocumentViewer } from "./PdfDocumentViewer";

const { getDocument } = vi.hoisted(() => ({ getDocument: vi.fn() }));

vi.mock("pdfjs-dist/legacy/build/pdf.mjs", () => ({
  GlobalWorkerOptions: {},
  getDocument,
}));

vi.mock("pdfjs-dist/legacy/build/pdf.worker.mjs?url", () => ({
  default: "pdf-worker-test-url",
}));

function renderViewer() {
  return render(
    <PdfDocumentViewer
      side="original"
      src="http://api.test/file.pdf"
      title="原版"
      diffs={[]}
      zoom={1}
      activeDiffId=""
      syncEnabled={false}
      onScrollRatio={vi.fn()}
      onActivateDiff={vi.fn()}
    />,
  );
}

describe("PdfDocumentViewer load errors", () => {
  it.each([
    ["PasswordException", "PDF 已加密，请上传未加密版本。"],
    ["InvalidPDFException", "PDF 已损坏或格式无效。"],
    ["UnexpectedResponseException", "无法获取 PDF 文件，请检查后端服务或网络连接。"],
    ["UnknownErrorException", "PDF 载入失败，请确认文件有效后重试。"],
  ])("shows an actionable message for %s", async (name, message) => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    getDocument.mockReturnValueOnce({
      promise: Promise.reject({ name }),
      destroy: vi.fn(),
    });

    renderViewer();

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });
});
