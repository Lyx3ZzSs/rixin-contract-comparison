import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, Download, Eye, EyeOff, PanelRightOpen, ZoomIn, ZoomOut } from "lucide-react";

import { PdfDocumentViewer, type PdfDocumentViewerHandle } from "../components/PdfDocumentViewer";
import { getDiffs, getTask, toApiUrl } from "../lib/api";
import type { CompareTask, DiffItem, DiffType } from "../types";

interface ResultPageProps {
  taskId: string;
  onBack: () => void;
}

export function ResultPage({ taskId, onBack }: ResultPageProps) {
  const [task, setTask] = useState<CompareTask | null>(null);
  const [diffs, setDiffs] = useState<DiffItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [isOriginalVisible, setIsOriginalVisible] = useState(true);
  const [isSyncScroll, setIsSyncScroll] = useState(true);
  const [isAuditPanelOpen, setIsAuditPanelOpen] = useState(false);
  const [diffFilter, setDiffFilter] = useState<DiffFilter>("ALL");
  const [zoom, setZoom] = useState(1);
  const [activeDiffId, setActiveDiffId] = useState("");
  const [activeAuditItemId, setActiveAuditItemId] = useState("");
  const originalViewerRef = useRef<PdfDocumentViewerHandle | null>(null);
  const compareViewerRef = useRef<PdfDocumentViewerHandle | null>(null);

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

  const linkedDiffs = useMemo(
    () => diffs.filter((diff) => (diff.original_evidence?.length ?? 0) > 0 || (diff.compare_evidence?.length ?? 0) > 0),
    [diffs],
  );
  const auditItems = useMemo(() => buildAuditItems(diffs), [diffs]);
  const auditStats = useMemo(() => buildAuditStats(auditItems), [auditItems]);
  const filteredAuditItems = useMemo(
    () => (diffFilter === "ALL" ? auditItems : auditItems.filter((item) => item.type === diffFilter)),
    [auditItems, diffFilter],
  );

  function handleZoomOut() {
    setZoom((value) => Math.max(0.5, Number((value - 0.1).toFixed(2))));
  }

  function handleZoomIn() {
    setZoom((value) => Math.min(1.6, Number((value + 0.1).toFixed(2))));
  }

  function handleScrollRatio(ratio: number, source: "original" | "compare") {
    if (!isSyncScroll) {
      return;
    }
    if (source === "original") {
      compareViewerRef.current?.syncScrollFrom(ratio);
      return;
    }
    originalViewerRef.current?.syncScrollFrom(ratio);
  }

  function focusDiff(diffId: string) {
    const diff = diffs.find((item) => item.diff_id === diffId);
    if (!diff) {
      return;
    }
    setActiveDiffId(diffId);
    originalViewerRef.current?.scrollToDiff(diff);
    compareViewerRef.current?.scrollToDiff(diff);
  }

  function focusAuditItem(item: AuditChangeItem) {
    setActiveAuditItemId(item.id);
    focusDiff(item.diffId);
  }

  async function downloadPdfFile(url: string, filename: string) {
    if (!url) {
      return;
    }
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`文件下载失败 (${response.status})`);
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  }

  if (isLoading) {
    return <StateScreen title="正在载入审查结果" detail={`任务 ${taskId}`} />;
  }

  if (error || !task) {
    return <StateScreen title="无法打开审查结果" detail={error || "任务不存在。"} onBack={onBack} />;
  }

  return (
    <section className="result-console" aria-labelledby="result-title">
      <section
        className={isAuditPanelOpen ? "pdf-review-page audit-open" : "pdf-review-page audit-closed"}
        aria-label="PDF 在线对比预览"
      >
        <header className="pdf-review-bar">
          <div className="pdf-file-meta">
            <button className="preview-visibility" type="button" onClick={() => setIsOriginalVisible((value) => !value)}>
              {isOriginalVisible ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
              {isOriginalVisible ? "隐藏原版" : "显示原版"}
            </button>
            <span className="pdf-tag original">原版</span>
            <strong title={task.original_filename}>{task.original_filename || "原版文件.pdf"}</strong>
            <button
              className="pdf-open-link"
              type="button"
              aria-label="下载原版文件"
              onClick={() =>
                void downloadPdfFile(toApiUrl(task.original_pdf_url), task.original_filename || "原版文件.pdf").catch(console.error)
              }
            >
              <Download aria-hidden="true" />
            </button>
          </div>

          <h1 id="result-title" className="pdf-axis-title">
            比对轴
          </h1>

          <div className="pdf-file-meta compare-file-meta">
            <span className="pdf-tag compare">新版</span>
            <strong title={task.compare_filename}>{task.compare_filename || "新版文件.pdf"}</strong>
            <button
              className="pdf-open-link"
              type="button"
              aria-label="下载新版文件"
              onClick={() =>
                void downloadPdfFile(toApiUrl(task.compare_pdf_url), task.compare_filename || "新版文件.pdf").catch(console.error)
              }
            >
              <Download aria-hidden="true" />
            </button>
          </div>
        </header>

        <section className="pdf-compare" aria-label="左右合同 PDF 预览">
          <PdfDocumentViewer
            ref={originalViewerRef}
            side="original"
            title="原版"
            src={toApiUrl(task.original_highlight_pdf_url || task.original_pdf_url)}
            diffs={diffs}
            zoom={zoom}
            activeDiffId={activeDiffId}
            hidden={!isOriginalVisible}
            syncEnabled={isSyncScroll}
            onScrollRatio={handleScrollRatio}
            onActivateDiff={focusDiff}
          />
          <div className="compare-axis" aria-label="差异比对轴">
            {linkedDiffs.length === 0 ? (
              <>
                <i className="axis-marker teal" />
                <i className="axis-marker amber" />
              </>
            ) : (
              linkedDiffs.map((diff, index) => (
                <button
                  key={diff.diff_id}
                  className={diff.diff_id === activeDiffId ? "axis-marker active" : "axis-marker"}
                  type="button"
                  style={{ top: `${((index + 1) / (linkedDiffs.length + 1)) * 100}%` }}
                  aria-label={`定位差异 ${diff.diff_id}`}
                  onClick={() => focusDiff(diff.diff_id)}
                />
              ))
            )}
          </div>
          <PdfDocumentViewer
            ref={compareViewerRef}
            side="compare"
            title="新版"
            src={toApiUrl(task.compare_highlight_pdf_url || task.compare_pdf_url)}
            diffs={diffs}
            zoom={zoom}
            activeDiffId={activeDiffId}
            syncEnabled={isSyncScroll}
            onScrollRatio={handleScrollRatio}
            onActivateDiff={focusDiff}
          />
        </section>

        <div className="pdf-floating-tools" aria-label="PDF 预览工具条">
          <button className="zoom-button zoom-out" type="button" aria-label="缩小预览" onClick={handleZoomOut}>
            <ZoomOut aria-hidden="true" />
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button className="zoom-button zoom-in" type="button" aria-label="放大预览" onClick={handleZoomIn}>
            <ZoomIn aria-hidden="true" />
          </button>
          <button
            className={isSyncScroll ? "sync-switch active" : "sync-switch"}
            type="button"
            onClick={() => setIsSyncScroll((value) => !value)}
            aria-label="切换同屏滚动"
            aria-pressed={isSyncScroll}
          >
            <span />
          </button>
          <strong>同屏滚动</strong>
        </div>
        <AuditPanel
          activeAuditItemId={activeAuditItemId}
          filter={diffFilter}
          items={filteredAuditItems}
          isOpen={isAuditPanelOpen}
          stats={auditStats}
          onClose={() => setIsAuditPanelOpen(false)}
          onFilterChange={setDiffFilter}
          onSelectItem={focusAuditItem}
        />
        <button
          className="audit-panel-rail"
          type="button"
          aria-label="展开审计侧栏"
          aria-hidden={isAuditPanelOpen}
          disabled={isAuditPanelOpen}
          tabIndex={isAuditPanelOpen ? -1 : undefined}
          onClick={() => setIsAuditPanelOpen(true)}
        >
          <PanelRightOpen aria-hidden="true" />
          审计
        </button>
      </section>
    </section>
  );
}

type DiffFilter = "ALL" | DiffType;

interface DiffStats {
  all: number;
  add: number;
  delete: number;
  modify: number;
}

interface AuditChangeItem {
  id: string;
  diffId: string;
  type: DiffType;
  title: string;
  summary: string;
}

function buildAuditItems(diffs: DiffItem[]): AuditChangeItem[] {
  return diffs.flatMap((diff) => auditItemsForDiff(diff));
}

function auditItemsForDiff(diff: DiffItem): AuditChangeItem[] {
  const originalEvidence = diff.original_evidence ?? [];
  const compareEvidence = diff.compare_evidence ?? [];
  const hasTypedEvidence = [...originalEvidence, ...compareEvidence].some((evidence) => Boolean(evidence.highlight_type));

  if (!hasTypedEvidence) {
    return [auditItem(diff, diff.diff_type, diffSummary(diff))];
  }

  const items: AuditChangeItem[] = [];
  const addText = evidenceText(compareEvidence, "ADD");
  const deleteText = evidenceText(originalEvidence, "DELETE");
  const originalModifyText = evidenceText(originalEvidence, "MODIFY");
  const compareModifyText = evidenceText(compareEvidence, "MODIFY");
  if (addText) {
    items.push(auditItem(diff, "ADD", addText));
  }
  if (deleteText) {
    items.push(auditItem(diff, "DELETE", deleteText));
  }
  if (originalModifyText || compareModifyText) {
    items.push(auditItem(diff, "MODIFY", modifySummary(originalModifyText, compareModifyText)));
  }
  return items;
}

function auditItem(diff: DiffItem, type: DiffType, summary: string): AuditChangeItem {
  return {
    id: `${diff.diff_id}:${type}`,
    diffId: diff.diff_id,
    type,
    title: diff.title || diff.clause_no || diff.diff_id,
    summary: compactText(summary || diffSummary(diff)),
  };
}

function buildAuditStats(items: AuditChangeItem[]): DiffStats {
  const stats = items.reduce(
    (nextStats, item) => {
      if (item.type === "ADD") {
        nextStats.add += 1;
      } else if (item.type === "DELETE") {
        nextStats.delete += 1;
      } else if (item.type === "MODIFY") {
        nextStats.modify += 1;
      }
      return nextStats;
    },
    { all: 0, add: 0, delete: 0, modify: 0 },
  );
  stats.all = items.length;
  return stats;
}

function evidenceText(evidenceList: NonNullable<DiffItem["compare_evidence"]>, type: DiffType): string {
  return compactText(evidenceList.filter((evidence) => evidence.highlight_type === type).map((evidence) => evidence.text).join(" "));
}

function modifySummary(originalText: string, compareText: string): string {
  if (originalText && compareText) {
    return `原文：${originalText} 修改后：${compareText}`;
  }
  return originalText || compareText;
}

function AuditPanel({
  activeAuditItemId,
  filter,
  items,
  isOpen,
  stats,
  onClose,
  onFilterChange,
  onSelectItem,
}: {
  activeAuditItemId: string;
  filter: DiffFilter;
  items: AuditChangeItem[];
  isOpen: boolean;
  stats: DiffStats;
  onClose: () => void;
  onFilterChange: (filter: DiffFilter) => void;
  onSelectItem: (item: AuditChangeItem) => void;
}) {
  const statItems: Array<{ filter: DiffFilter; label: string; value: number }> = [
    { filter: "ALL", label: "全部", value: stats.all },
    { filter: "DELETE", label: "删除", value: stats.delete },
    { filter: "ADD", label: "新增", value: stats.add },
    { filter: "MODIFY", label: "修改", value: stats.modify },
  ];
  const hiddenTabIndex = isOpen ? undefined : -1;

  return (
    <aside className={isOpen ? "audit-panel is-open" : "audit-panel is-closed"} aria-label="审计统计侧栏" aria-hidden={!isOpen}>
      <div className="audit-panel-head">
        <div>
          <p className="eyebrow">审计统计</p>
          <h2>当前文档改动</h2>
        </div>
        <button className="audit-close-button" type="button" aria-label="收起审计侧栏" tabIndex={hiddenTabIndex} onClick={onClose}>
          <ChevronRight aria-hidden="true" />
        </button>
      </div>

      <div className="audit-stat-grid" aria-label="差异类型统计">
        {statItems.map((item) => (
          <button
            key={item.filter}
            className={filter === item.filter ? "audit-stat active" : "audit-stat"}
            type="button"
            onClick={() => onFilterChange(item.filter)}
            aria-label={`筛选${item.label}差异`}
            aria-pressed={filter === item.filter}
            tabIndex={hiddenTabIndex}
          >
            <strong>{item.value}</strong>
            <span>{item.label}</span>
          </button>
        ))}
      </div>

      <div className="audit-filter-row">
        <strong>共 {items.length} 个改动点</strong>
        <span>{filter === "ALL" ? "全部类型" : diffTypeLabel(filter)}</span>
      </div>

      <div className="audit-diff-list">
        {items.length === 0 ? (
          <div className="audit-empty">未发现改动点。</div>
        ) : (
          items.map((item) => (
            <AuditDiffCard
              key={item.id}
              active={item.id === activeAuditItemId}
              item={item}
              tabIndex={hiddenTabIndex}
              onSelect={onSelectItem}
            />
          ))
        )}
      </div>
    </aside>
  );
}

function AuditDiffCard({
  active,
  item,
  tabIndex,
  onSelect,
}: {
  active: boolean;
  item: AuditChangeItem;
  tabIndex: number | undefined;
  onSelect: (item: AuditChangeItem) => void;
}) {
  return (
    <button
      className={active ? "audit-diff-card active" : "audit-diff-card"}
      type="button"
      aria-label={`审计定位改动 ${item.id}`}
      tabIndex={tabIndex}
      onClick={() => onSelect(item)}
    >
      <span className={`audit-type-badge ${item.type.toLowerCase()}`}>{diffTypeLabel(item.type)}</span>
      <strong>{item.title}</strong>
      <span>{item.summary}</span>
    </button>
  );
}

function diffTypeLabel(type: DiffFilter): string {
  if (type === "ADD") {
    return "新增";
  }
  if (type === "DELETE") {
    return "删除";
  }
  if (type === "MODIFY") {
    return "修改";
  }
  return "全部";
}

function diffSummary(diff: DiffItem): string {
  const summary = diff.readable_change || diff.compare_snippet || diff.original_snippet || diff.compare_text || diff.original_text;
  return compactText(summary || "暂无摘要");
}

function compactText(value: string): string {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length > 92 ? `${text.slice(0, 92)}...` : text;
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
