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
      accessToken="test-token"
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

function renderViewerWithoutToken() {
  return render(
    <PdfDocumentViewer
      side="original"
      src="http://api.test/file.pdf"
      accessToken=""
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
  it("loads the PDF with the supplied Bearer token", () => {
    getDocument.mockReturnValueOnce({ promise: new Promise(() => undefined), destroy: vi.fn() });
    renderViewer();
    expect(getDocument).toHaveBeenCalledWith({
      url: "http://api.test/file.pdf",
      httpHeaders: { Authorization: "Bearer test-token" },
    });
  });

  it("loads the PDF without authorization headers in disabled mode", () => {
    getDocument.mockReturnValueOnce({ promise: new Promise(() => undefined), destroy: vi.fn() });
    renderViewerWithoutToken();
    expect(getDocument).toHaveBeenCalledWith({ url: "http://api.test/file.pdf" });
  });

  it("does not reload the PDF when silent renewal replaces the access token", () => {
    const destroy = vi.fn();
    getDocument.mockReturnValue({ promise: new Promise(() => undefined), destroy });
    const { rerender } = renderViewer();
    const callsAfterInitialLoad = getDocument.mock.calls.length;

    rerender(
      <PdfDocumentViewer
        side="original"
        src="http://api.test/file.pdf"
        accessToken="renewed-token"
        title="原版"
        diffs={[]}
        zoom={1}
        activeDiffId=""
        syncEnabled={false}
        onScrollRatio={vi.fn()}
        onActivateDiff={vi.fn()}
      />,
    );

    expect(getDocument).toHaveBeenCalledTimes(callsAfterInitialLoad);
    expect(destroy).not.toHaveBeenCalled();
  });

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
