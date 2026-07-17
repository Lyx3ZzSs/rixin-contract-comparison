import { ChevronLeft, ChevronRight, RotateCcw, Search } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ProgressRing } from "../components/ProgressRing";
import { getCompareRecords, retryCompareTask, toApiUrl } from "../lib/api";
import { downloadAuthenticatedFile } from "../lib/authFetch";
import { useRecordProgressSSE } from "../lib/hooks";
import { canRetryTask, taskStatusLabel } from "../lib/taskStatus";
import type { CompareRecordListResponse, CompareRecordQuery, CompareRecordSummary } from "../types";

interface ComparisonRecordsPageProps {
  onOpenTask: (taskId: string) => void;
  onCreateComparison: () => void;
}

const PAGE_SIZE = 10;

interface RecordQueryState {
  page: number;
  startDate: string;
  endDate: string;
}

const emptyPagination = {
  total: 0,
  page: 1,
  page_size: PAGE_SIZE,
  total_pages: 0,
};

export function ComparisonRecordsPage({ onOpenTask, onCreateComparison }: ComparisonRecordsPageProps) {
  const [records, setRecords] = useState<CompareRecordSummary[]>([]);
  const [pagination, setPagination] = useState(emptyPagination);
  const [query, setQuery] = useState<RecordQueryState>({ page: 1, startDate: "", endDate: "" });
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [syncNotice, setSyncNotice] = useState("");
  const [pendingRetryIds, setPendingRetryIds] = useState<Set<string>>(() => new Set());
  const pendingRetryIdsRef = useRef(new Set<string>());

  const buildApiQuery = useCallback((state: RecordQueryState): CompareRecordQuery => ({
    page: state.page,
    pageSize: PAGE_SIZE,
    startDate: state.startDate,
    endDate: state.endDate,
  }), []);

  const applyPayload = useCallback((payload: CompareRecordListResponse) => {
    setRecords(payload.records);
    setPagination({
      total: payload.total,
      page: payload.page,
      page_size: payload.page_size,
      total_pages: payload.total_pages,
    });
  }, []);

  useEffect(() => {
    let isCurrent = true;
    setIsLoading(true);
    setError("");
    setSyncNotice("");
    void getCompareRecords(buildApiQuery(query))
      .then((payload) => {
        if (isCurrent) applyPayload(payload);
      })
      .catch((err) => {
        if (isCurrent) {
          setRecords([]);
          setPagination(emptyPagination);
          setError(err instanceof Error ? err.message : "对比记录加载失败。");
        }
      })
      .finally(() => {
        if (isCurrent) setIsLoading(false);
      });
    return () => {
      isCurrent = false;
    };
  }, [applyPayload, buildApiQuery, query]);

  const refreshCurrentPage = useCallback(async () => {
    try {
      const payload = await getCompareRecords(buildApiQuery(query));
      applyPayload(payload);
      setSyncNotice("");
    } catch {
      setSyncNotice("刷新失败，任务仍在后台同步。");
    }
  }, [applyPayload, buildApiQuery, query]);

  // SSE 实时更新 PROCESSING 记录进度
  useRecordProgressSSE(
    records,
    (update) => {
      setRecords((prev) =>
        prev.map((record) => record.task_id === update.task_id ? {
          ...record,
          progress_percent: update.progress_percent,
          stage: update.stage,
          status: update.status,
          revision: Math.max(record.revision ?? 0, Number(update.revision) || 0),
          terminal_reason: "terminal_reason" in update ? update.terminal_reason : record.terminal_reason,
          report_revision: "report_revision" in update ? update.report_revision : record.report_revision,
        } : record),
      );
    },
    () => {
      // 记录完成时刷新完整列表（获取 diff_count、report_url 等最终字段）
      void refreshCurrentPage();
    },
  );

  const retryRecord = useCallback(async (taskId: string) => {
    if (pendingRetryIdsRef.current.has(taskId)) return;
    pendingRetryIdsRef.current.add(taskId);
    setPendingRetryIds(new Set(pendingRetryIdsRef.current));
    setSyncNotice("");
    try {
      await retryCompareTask(taskId);
      setRecords((current) => current.map((record) => record.task_id === taskId ? {
        ...record,
        status: "PROCESSING",
        terminal_reason: "NONE",
        retry_eligible: false,
        stage: "排队中",
        progress_percent: 3,
      } : record));
      void refreshCurrentPage();
    } catch (retryError) {
      setSyncNotice(retryError instanceof Error ? retryError.message : "重试失败。");
    } finally {
      pendingRetryIdsRef.current.delete(taskId);
      setPendingRetryIds(new Set(pendingRetryIdsRef.current));
    }
  }, [refreshCurrentPage]);

  const applyFilters = () => {
    setQuery({ page: 1, startDate, endDate });
  };

  const resetFilters = () => {
    setStartDate("");
    setEndDate("");
    setQuery({ page: 1, startDate: "", endDate: "" });
  };

  const goToPreviousPage = () => {
    setQuery((current) => ({ ...current, page: Math.max(1, current.page - 1) }));
  };

  const goToNextPage = () => {
    setQuery((current) => ({ ...current, page: current.page + 1 }));
  };

  const hasPreviousPage = query.page > 1;
  const hasNextPage = pagination.total_pages > 0 && query.page < pagination.total_pages;

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
        <div className="records-toolbar" aria-label="对比记录筛选">
          <label>
            <span>创建开始日期</span>
            <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </label>
          <label>
            <span>创建结束日期</span>
            <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </label>
          <button type="button" className="records-filter-primary" onClick={applyFilters}>
            <Search size={15} aria-hidden="true" />
            查询
          </button>
          <button type="button" className="records-filter-secondary" onClick={resetFilters}>
            <RotateCcw size={15} aria-hidden="true" />
            重置
          </button>
        </div>

        {syncNotice && <p role="status">{syncNotice}</p>}

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
                    <span>{formatDateTime(record.created_at || record.updated_at)}</span>
                    <span className={`record-status ${record.status.toLowerCase()}`}>
                      {taskStatusLabel(record.status, record.terminal_reason)}
                    </span>
                  </div>
                </div>
                <div className="record-stats" aria-label="差异统计">
                  <span>
                    <b>{record.diff_count}</b>
                    差异
                  </span>
                </div>
                <div className="record-actions">
                  {record.report_url && (
                    <button
                      type="button"
                      onClick={() => void downloadAuthenticatedFile(
                        toApiUrl(record.report_url),
                        `合同差异分析报告-${record.task_id}.pdf`,
                      ).catch((downloadError) => setError(downloadError instanceof Error ? downloadError.message : "报告下载失败。"))}
                    >
                      报告
                    </button>
                  )}
                  {canRetryTask(record.status, record.terminal_reason, record.retry_eligible) && (
                    <button
                      type="button"
                      disabled={pendingRetryIds.has(record.task_id)}
                      onClick={() => void retryRecord(record.task_id)}
                    >
                      {pendingRetryIds.has(record.task_id) ? "重试中..." : "重试"}
                    </button>
                  )}
                  {record.status === "PROCESSING" ? (
                    <RecordProgress record={record} />
                  ) : (
                    <button type="button" onClick={() => onOpenTask(record.task_id)}>
                      查看结果
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}

        {!isLoading && !error && (
          <div className="records-pagination" aria-label="对比记录分页">
            <span>
              共 {pagination.total} 条
              {pagination.total_pages > 0 ? `，第 ${pagination.page} / ${pagination.total_pages} 页` : ""}
            </span>
            <div>
              <button type="button" onClick={goToPreviousPage} disabled={!hasPreviousPage}>
                <ChevronLeft size={15} aria-hidden="true" />
                上一页
              </button>
              <button type="button" onClick={goToNextPage} disabled={!hasNextPage}>
                下一页
                <ChevronRight size={15} aria-hidden="true" />
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function RecordProgress({ record }: { record: CompareRecordSummary }) {
  const progressPercent = Math.max(0, Math.min(100, record.progress_percent || 0));

  return (
    <div className="record-progress">
      <ProgressRing value={progressPercent} label={record.stage || "处理中"} size="compact" />
      <div className="record-progress-label">
        <span>{record.stage || "处理中"}</span>
      </div>
    </div>
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
