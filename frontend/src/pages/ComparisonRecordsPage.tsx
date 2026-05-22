import { useEffect, useState } from "react";

import { getCompareRecords, toApiUrl } from "../lib/api";
import type { CompareRecordSummary, TaskStatus } from "../types";

interface ComparisonRecordsPageProps {
  onOpenTask: (taskId: string) => void;
  onCreateComparison: () => void;
}

const statusLabels: Record<TaskStatus, string> = {
  PROCESSING: "处理中",
  COMPLETED: "已完成",
  FAILED: "失败",
};

export function ComparisonRecordsPage({ onOpenTask, onCreateComparison }: ComparisonRecordsPageProps) {
  const [records, setRecords] = useState<CompareRecordSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let isCurrent = true;

    async function loadRecords() {
      setIsLoading(true);
      setError("");
      try {
        const payload = await getCompareRecords();
        if (isCurrent) {
          setRecords(payload);
        }
      } catch (err) {
        if (isCurrent) {
          setError(err instanceof Error ? err.message : "对比记录加载失败。");
        }
      } finally {
        if (isCurrent) {
          setIsLoading(false);
        }
      }
    }

    void loadRecords();
    return () => {
      isCurrent = false;
    };
  }, []);

  return (
    <section className="records-workspace" aria-labelledby="records-title">
      <header className="records-header">
        <div>
          <span>合同智能对比</span>
          <h1 id="records-title">对比记录</h1>
        </div>
        <button type="button" onClick={onCreateComparison}>
          新建合同对比
        </button>
      </header>

      <div className="records-panel">
        {isLoading ? (
          <div className="records-state" role="status">
            正在加载对比记录...
          </div>
        ) : error ? (
          <div className="records-state error" role="alert">
            <strong>记录加载失败</strong>
            <span>{error}</span>
          </div>
        ) : records.length === 0 ? (
          <div className="records-state">
            <strong>暂无对比记录</strong>
            <span>完成一次合同对比后，记录会显示在这里。</span>
          </div>
        ) : (
          <div className="records-list" aria-label="合同对比记录列表">
            {records.map((record) => (
              <article className="record-row" key={record.task_id}>
                <div className="record-main">
                  <div className="record-title-line">
                    <strong>{record.original_filename || "原版合同"}</strong>
                    <span aria-hidden="true" />
                    <strong>{record.compare_filename || "新版合同"}</strong>
                  </div>
                  <div className="record-meta">
                    <span>{record.task_id}</span>
                    <span>{formatDateTime(record.updated_at || record.created_at)}</span>
                    <span className={`record-status ${record.status.toLowerCase()}`}>{statusLabels[record.status]}</span>
                  </div>
                </div>
                <div className="record-stats" aria-label="差异统计">
                  <span>
                    <b>{record.diff_count}</b>
                    差异
                  </span>
                  <span>
                    <b>{record.high_risk_count}</b>
                    高风险
                  </span>
                  <span>
                    <b>{record.medium_risk_count}</b>
                    中风险
                  </span>
                </div>
                <div className="record-actions">
                  {record.report_url && (
                    <a href={toApiUrl(record.report_url)} target="_blank" rel="noreferrer">
                      报告
                    </a>
                  )}
                  <button type="button" onClick={() => onOpenTask(record.task_id)}>
                    查看结果
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function formatDateTime(value: string): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
