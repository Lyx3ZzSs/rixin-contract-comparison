import { useEffect, useMemo, useState } from "react";

import { getDiffs, getTask, toApiUrl } from "../lib/api";
import type { CompareTask, DiffItem, RiskLevel } from "../types";

interface ResultPageProps {
  taskId: string;
  onBack: () => void;
}

export function ResultPage({ taskId, onBack }: ResultPageProps) {
  const [task, setTask] = useState<CompareTask | null>(null);
  const [diffs, setDiffs] = useState<DiffItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);
    setError("");

    Promise.all([getTask(taskId), getDiffs(taskId)])
      .then(([taskPayload, diffPayload]) => {
        if (!isMounted) {
          return;
        }
        setTask(taskPayload);
        setDiffs(diffPayload);
      })
      .catch((err) => {
        if (isMounted) {
          setError(err instanceof Error ? err.message : "读取任务失败。");
        }
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [taskId]);

  const topRisk = useMemo(() => {
    if (!task) {
      return "LOW" satisfies RiskLevel;
    }
    if (task.high_risk_count > 0) {
      return "HIGH" satisfies RiskLevel;
    }
    if (task.medium_risk_count > 0) {
      return "MEDIUM" satisfies RiskLevel;
    }
    return "LOW" satisfies RiskLevel;
  }, [task]);

  if (isLoading) {
    return <StateScreen title="正在载入审查结果" detail={`任务 ${taskId}`} />;
  }

  if (error || !task) {
    return <StateScreen title="无法打开审查结果" detail={error || "任务不存在。"} onBack={onBack} />;
  }

  return (
    <section className="result-console" aria-labelledby="result-title">
      <header className="result-header">
        <button className="ghost-button" type="button" onClick={onBack}>
          返回上传
        </button>
        <div>
          <p className="eyebrow">任务编号 {task.task_id}</p>
          <h1 id="result-title">差异审查结果</h1>
        </div>
        <a className="download-button" href={toApiUrl(task.report_url)}>
          下载报告
        </a>
      </header>

      <section className="risk-bar" aria-label="风险统计">
        <Metric label="总差异" value={task.diff_count} tone="neutral" />
        <Metric label="高风险" value={task.high_risk_count} tone="high" />
        <Metric label="中风险" value={task.medium_risk_count} tone="medium" />
        <Metric label="低风险" value={task.low_risk_count} tone="low" />
        <div className={`risk-beacon ${topRisk.toLowerCase()}`}>{riskText(topRisk)}</div>
      </section>

      <section className="pdf-compare" aria-label="高亮 PDF 预览">
        <PdfPane title="原合同" filename={task.original_filename} src={toApiUrl(task.original_highlight_pdf_url)} />
        <PdfPane title="对比合同" filename={task.compare_filename} src={toApiUrl(task.compare_highlight_pdf_url)} />
      </section>

      <section className="analysis-layout">
        <aside className="summary-panel">
          <p className="panel-label">AI 摘要</p>
          <p>{task.ai_summary || "未生成摘要。"}</p>
          {task.errors.length > 0 && (
            <div className="error-stack">
              {task.errors.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </div>
          )}
        </aside>

        <div className="diff-list" aria-label="差异列表">
          {diffs.length === 0 ? (
            <article className="diff-card">
              <h2>未发现差异</h2>
              <p>两份合同在当前解析和匹配规则下没有形成差异项。</p>
            </article>
          ) : (
            diffs.map((diff, index) => <DiffCard key={diff.diff_id} diff={diff} index={index + 1} taskId={taskId} />)
          )}
        </div>
      </section>
    </section>
  );
}

function StateScreen({ title, detail, onBack }: { title: string; detail: string; onBack?: () => void }) {
  return (
    <section className="state-screen">
      <p className="eyebrow">合同审查系统</p>
      <h1>{title}</h1>
      <p>{detail}</p>
      {onBack && (
        <button className="primary-action" type="button" onClick={onBack}>
          返回上传
        </button>
      )}
    </section>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PdfPane({ title, filename, src }: { title: string; filename: string; src: string }) {
  return (
    <article className="pdf-pane">
      <header>
        <span>{title}</span>
        <strong title={filename}>{filename || "PDF"}</strong>
      </header>
      {src ? <iframe title={`${title}高亮 PDF`} src={src} /> : <div className="empty-pane">暂无高亮 PDF</div>}
    </article>
  );
}

function DiffCard({ diff, index, taskId }: { diff: DiffItem; index: number; taskId: string }) {
  const risk = diff.ai_analysis?.risk_level ?? "LOW";
  const originalShot = screenshotUrl(taskId, diff.original_screenshot_url, diff.original_screenshot);
  const compareShot = screenshotUrl(taskId, diff.compare_screenshot_url, diff.compare_screenshot);

  return (
    <article className="diff-card">
      <header className="diff-card-header">
        <div>
          <p className="panel-label">#{String(index).padStart(2, "0")} {diff.diff_type}</p>
          <h2>{diff.title || diff.clause_no || diff.diff_id}</h2>
        </div>
        <span className={`risk-pill ${risk.toLowerCase()}`}>{riskText(risk)}</span>
      </header>

      <p className="readable-change">{diff.readable_change || diff.ai_analysis?.change_summary || "差异内容待复核。"}</p>

      <div className="clause-grid">
        <TextPanel title="原合同片段" text={diff.original_snippet || diff.original_text} />
        <TextPanel title="对比合同片段" text={diff.compare_snippet || diff.compare_text} />
      </div>

      {diff.ai_analysis && (
        <div className="ai-detail">
          <span>{diff.ai_analysis.contract_element}</span>
          <strong>风险分 {diff.ai_analysis.risk_score}</strong>
          <p>{diff.ai_analysis.risk_explanation}</p>
          <p>{diff.ai_analysis.review_suggestion}</p>
        </div>
      )}

      {(originalShot || compareShot) && (
        <div className="screenshot-grid">
          {originalShot && <img src={originalShot} alt="原合同差异截图" />}
          {compareShot && <img src={compareShot} alt="对比合同差异截图" />}
        </div>
      )}
    </article>
  );
}

function TextPanel({ title, text }: { title: string; text: string }) {
  return (
    <section className="text-panel">
      <span>{title}</span>
      <p>{text || "无对应内容"}</p>
    </section>
  );
}

function screenshotUrl(taskId: string, url?: string, path?: string): string {
  if (url) {
    return toApiUrl(url);
  }
  if (!path) {
    return "";
  }
  const filename = path.split(/[\\/]/).filter(Boolean).at(-1);
  return filename ? toApiUrl(`/api/compare/${taskId}/screenshot/${filename}`) : "";
}

function riskText(risk: RiskLevel): string {
  return {
    HIGH: "高风险",
    MEDIUM: "中风险",
    LOW: "低风险",
  }[risk];
}
