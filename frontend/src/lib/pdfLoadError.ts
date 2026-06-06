export function formatPdfLoadError(error: unknown): string {
  const name = errorName(error);
  const details = errorDetails(error);

  if (name === "PasswordException") {
    return "PDF 已加密，请上传未加密版本。";
  }
  if (name === "InvalidPDFException") {
    return "PDF 已损坏或格式无效。";
  }
  if (
    name === "MissingPDFException" ||
    name === "UnexpectedResponseException" ||
    name === "ResponseException"
  ) {
    return "无法获取 PDF 文件，请检查后端服务或网络连接。";
  }
  if (details.includes("toHex is not a function")) {
    return "当前浏览器不兼容 PDF 预览组件，请刷新页面后重试。";
  }
  return "PDF 载入失败，请确认文件有效后重试。";
}

function errorName(error: unknown): string {
  if (error && typeof error === "object" && "name" in error) {
    return String(error.name || "");
  }
  return "";
}

function errorDetails(error: unknown): string {
  if (error && typeof error === "object" && "details" in error) {
    return String(error.details || "");
  }
  return "";
}
