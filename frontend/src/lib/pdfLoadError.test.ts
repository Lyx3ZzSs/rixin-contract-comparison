import { describe, expect, it } from "vitest";

import { formatPdfLoadError } from "./pdfLoadError";

describe("formatPdfLoadError", () => {
  it("describes password protected PDFs", () => {
    expect(formatPdfLoadError({ name: "PasswordException" })).toBe("PDF 已加密，请上传未加密版本。");
  });

  it("describes invalid PDFs", () => {
    expect(formatPdfLoadError({ name: "InvalidPDFException" })).toBe("PDF 已损坏或格式无效。");
  });

  it("describes inaccessible PDF endpoints", () => {
    expect(formatPdfLoadError({ name: "UnexpectedResponseException" })).toBe(
      "无法获取 PDF 文件，请检查后端服务或网络连接。",
    );
    expect(formatPdfLoadError({ name: "ResponseException" })).toBe(
      "无法获取 PDF 文件，请检查后端服务或网络连接。",
    );
  });

  it("uses a safe fallback for unknown failures", () => {
    expect(formatPdfLoadError(new Error("worker failed"))).toBe("PDF 载入失败，请确认文件有效后重试。");
  });

  it("classifies incompatible PDF.js runtime APIs", () => {
    expect(
      formatPdfLoadError({
        name: "UnknownErrorException",
        details: "TypeError: hashOriginal.toHex is not a function",
      }),
    ).toBe("当前浏览器不兼容 PDF 预览组件，请刷新页面后重试。");
  });
});
