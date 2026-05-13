from __future__ import annotations


def build_home_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI 合同差异审查系统</title>
  <style>
    * { box-sizing: border-box; }
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --line: #d8dee7;
      --text: #172033;
      --muted: #657083;
      --panel: #ffffff;
      --accent: #1f6feb;
      --accent-dark: #185abc;
      --danger: #b42318;
      --ok: #087443;
      --warn: #9a6700;
    }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      min-height: 64px;
      padding: 0 28px;
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }
    .mark {
      display: grid;
      place-items: center;
      width: 34px;
      height: 34px;
      border-radius: 8px;
      background: #152238;
      color: #ffffff;
      font-weight: 700;
      letter-spacing: 0;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      line-height: 1.2;
      letter-spacing: 0;
    }
    nav {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    nav a {
      color: var(--accent);
      font-size: 14px;
      text-decoration: none;
    }
    main {
      display: grid;
      grid-template-columns: minmax(320px, 520px) minmax(360px, 1fr);
      gap: 22px;
      width: min(1180px, calc(100vw - 32px));
      margin: 22px auto;
      align-items: start;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .section-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 48px;
      padding: 0 16px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }
    h2 {
      margin: 0;
      font-size: 15px;
      letter-spacing: 0;
    }
    .body {
      padding: 16px;
    }
    form {
      display: grid;
      gap: 14px;
    }
    label {
      display: grid;
      gap: 7px;
      font-size: 13px;
      font-weight: 700;
    }
    input[type="file"] {
      width: 100%;
      min-height: 42px;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      color: var(--text);
      font-size: 13px;
    }
    .toggle {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 44px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font-size: 13px;
      font-weight: 700;
    }
    .toggle input {
      width: 18px;
      height: 18px;
      flex: 0 0 auto;
    }
    button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-height: 42px;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #ffffff;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover { background: var(--accent-dark); }
    button:disabled {
      cursor: not-allowed;
      background: #98a2b3;
    }
    .status {
      display: none;
      min-height: 42px;
      padding: 11px 12px;
      border-radius: 6px;
      border: 1px solid var(--line);
      font-size: 13px;
      line-height: 1.45;
      background: #f8fafc;
      color: var(--muted);
    }
    .status.show { display: block; }
    .status.error {
      border-color: #f2b8b5;
      background: #fff5f5;
      color: var(--danger);
    }
    .status.success {
      border-color: #9fd8bd;
      background: #f0fdf4;
      color: var(--ok);
    }
    .queue {
      display: grid;
      gap: 10px;
    }
    .row {
      display: grid;
      grid-template-columns: 140px 1fr;
      gap: 12px;
      padding: 12px 0;
      border-bottom: 1px solid #edf1f5;
      font-size: 13px;
      line-height: 1.45;
    }
    .row:last-child { border-bottom: 0; }
    .key {
      color: var(--muted);
      font-weight: 700;
    }
    .value {
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border-radius: 999px;
      background: #eef2ff;
      color: #3538cd;
      font-size: 12px;
      font-weight: 700;
    }
    .result-actions {
      display: none;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 14px;
    }
    .result-actions.show { display: flex; }
    .result-actions a {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 36px;
      padding: 0 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--text);
      background: #ffffff;
      text-decoration: none;
      font-size: 13px;
      font-weight: 700;
    }
    .result-actions a.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #ffffff;
    }
    .hint {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .warn {
      color: var(--warn);
      font-weight: 700;
    }
    @media (max-width: 860px) {
      header {
        align-items: flex-start;
        flex-direction: column;
        padding: 14px 16px;
      }
      nav { justify-content: flex-start; }
      main {
        grid-template-columns: 1fr;
        width: min(100vw - 24px, 620px);
        margin-top: 12px;
      }
      .row {
        grid-template-columns: 1fr;
        gap: 4px;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="mark">AI</div>
      <h1>AI 合同差异审查系统</h1>
    </div>
    <nav aria-label="快捷入口">
      <a href="/docs">API 文档</a>
      <a href="/health">健康检查</a>
    </nav>
  </header>
  <main>
    <section aria-labelledby="upload-title">
      <div class="section-title">
        <h2 id="upload-title">合同上传</h2>
        <span class="pill">PDF</span>
      </div>
      <div class="body">
        <form id="compareForm">
          <label>
            原合同
            <input id="originalFile" name="original_file" type="file" accept="application/pdf,.pdf" required>
          </label>
          <label>
            对比合同
            <input id="compareFile" name="compare_file" type="file" accept="application/pdf,.pdf" required>
          </label>
          <label class="toggle">
            <span>AI 风险分析</span>
            <input id="enableAi" name="enable_ai_analysis" type="checkbox" checked>
          </label>
          <button id="submitButton" type="submit">开始对比</button>
          <div id="status" class="status" role="status" aria-live="polite"></div>
        </form>
      </div>
    </section>
    <section aria-labelledby="result-title">
      <div class="section-title">
        <h2 id="result-title">处理结果</h2>
        <span id="resultState" class="pill">等待提交</span>
      </div>
      <div class="body">
        <div class="queue">
          <div class="row">
            <div class="key">任务编号</div>
            <div id="taskId" class="value">-</div>
          </div>
          <div class="row">
            <div class="key">差异数量</div>
            <div id="diffCount" class="value">-</div>
          </div>
          <div class="row">
            <div class="key">高风险数量</div>
            <div id="riskCount" class="value">-</div>
          </div>
          <div class="row">
            <div class="key">状态</div>
            <div id="taskStatus" class="value">-</div>
          </div>
        </div>
        <div id="actions" class="result-actions">
          <a id="previewLink" class="primary" href="#">打开预览</a>
          <a id="reportLink" href="#">下载报告</a>
        </div>
        <p class="hint"><span class="warn">注意：</span>请仅上传已批准用于测试或审查的合同文件。</p>
      </div>
    </section>
  </main>
  <script>
    const form = document.getElementById("compareForm");
    const submitButton = document.getElementById("submitButton");
    const statusBox = document.getElementById("status");
    const resultState = document.getElementById("resultState");
    const taskId = document.getElementById("taskId");
    const diffCount = document.getElementById("diffCount");
    const riskCount = document.getElementById("riskCount");
    const taskStatus = document.getElementById("taskStatus");
    const actions = document.getElementById("actions");
    const previewLink = document.getElementById("previewLink");
    const reportLink = document.getElementById("reportLink");

    function setStatus(message, type) {
      statusBox.textContent = message;
      statusBox.className = "status show" + (type ? " " + type : "");
    }

    function setLoading(isLoading) {
      submitButton.disabled = isLoading;
      submitButton.textContent = isLoading ? "正在对比" : "开始对比";
      resultState.textContent = isLoading ? "处理中" : "等待提交";
    }

    function showResult(payload) {
      taskId.textContent = payload.task_id || "-";
      diffCount.textContent = String(payload.diff_count ?? "-");
      riskCount.textContent = String(payload.high_risk_count ?? "-");
      taskStatus.textContent = payload.status || "-";
      resultState.textContent = payload.status || "已完成";
      previewLink.href = payload.preview_url || "#";
      reportLink.href = payload.report_url || "#";
      actions.classList.add("show");
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData();
      const original = document.getElementById("originalFile").files[0];
      const compare = document.getElementById("compareFile").files[0];
      data.append("original_file", original);
      data.append("compare_file", compare);
      data.append("enable_ai_analysis", document.getElementById("enableAi").checked ? "true" : "false");
      actions.classList.remove("show");
      setLoading(true);
      setStatus("文件已提交，正在生成差异和报告。");
      try {
        const response = await fetch("/api/compare", { method: "POST", body: data });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "合同对比失败。");
        }
        showResult(payload);
        setStatus("对比完成，正在打开预览。", "success");
        window.location.href = payload.preview_url;
      } catch (error) {
        resultState.textContent = "失败";
        setStatus(error.message || "合同对比失败。", "error");
      } finally {
        setLoading(false);
      }
    });
  </script>
</body>
</html>"""
