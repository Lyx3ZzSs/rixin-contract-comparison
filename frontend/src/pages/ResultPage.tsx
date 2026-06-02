import { useEffect, useMemo, useRef, useState } from "react";
import { Ban, ChevronRight, Download, Eye, EyeOff, PanelRightOpen, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";

import { PdfDocumentViewer, type PdfDocumentViewerHandle } from "../components/PdfDocumentViewer";
import { ProgressRing } from "../components/ProgressRing";
import { toApiUrl, updateAuditItemReview } from "../lib/api";
import { useTaskProgress } from "../lib/hooks";
import { navigateToComparisonRecords } from "../lib/routes";
import type { CompareTask, DiffItem, DiffType, ReviewStatus } from "../types";

interface ResultPageProps {
  taskId: string;
  onBack: () => void;
}

export function ResultPage({ taskId, onBack }: ResultPageProps) {
  const { task, diffs, isLoading, error, setTask } = useTaskProgress(taskId);
  const [isOriginalVisible, setIsOriginalVisible] = useState(true);
  const [isSyncScroll, setIsSyncScroll] = useState(true);
  const [isAuditPanelOpen, setIsAuditPanelOpen] = useState(false);
  const [isReportDownloading, setIsReportDownloading] = useState(false);
  const [reportDownloadError, setReportDownloadError] = useState("");
  const [diffFilter, setDiffFilter] = useState<DiffFilter>("ALL");
  const [zoom, setZoom] = useState(1);
  const [activeDiffId, setActiveDiffId] = useState("");
  const [activeAuditItemId, setActiveAuditItemId] = useState("");
  const [reviewSavingAuditItemId, setReviewSavingAuditItemId] = useState("");
  const [reviewError, setReviewError] = useState("");
  const originalViewerRef = useRef<PdfDocumentViewerHandle | null>(null);
  const compareViewerRef = useRef<PdfDocumentViewerHandle | null>(null);

  const auditItems = useMemo(
    () => buildAuditItems(diffs, task?.audit_item_reviews ?? {}),
    [diffs, task?.audit_item_reviews],
  );
  const axisMarkers = useMemo(() => buildAxisMarkers(auditItems), [auditItems]);
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
    if (!isOriginalVisible) {
      return;
    }
    originalViewerRef.current?.syncScrollFrom(ratio);
  }

  function focusDiff(diffId: string, auditItemId?: string) {
    const diff = diffs.find((item) => item.diff_id === diffId);
    if (!diff) {
      return;
    }
    setActiveDiffId(diffId);
    setActiveAuditItemId((currentId) => {
      if (auditItemId !== undefined) {
        return auditItemId;
      }
      const isCurrentSameDiff = auditItems.some((item) => item.id === currentId && item.diffId === diffId);
      return isCurrentSameDiff ? currentId : auditItems.find((item) => item.diffId === diffId)?.id ?? "";
    });
    if (isOriginalVisible) {
      originalViewerRef.current?.scrollToDiff(diff);
    }
    compareViewerRef.current?.scrollToDiff(diff);
  }

  function focusAuditItem(item: AuditChangeItem) {
    focusDiff(item.diffId, item.id);
  }

  async function handleReview(item: AuditChangeItem, status: ReviewStatus) {
    setReviewSavingAuditItemId(item.id);
    setReviewError("");
    try {
      const payload = await updateAuditItemReview(taskId, item.id, {
        review_status: status,
        review_comment: item.reviewComment ?? "",
        reviewed_by: "local_reviewer",
      });
      setTask((currentTask) =>
        currentTask
          ? {
              ...currentTask,
              audit_item_reviews: {
                ...(currentTask.audit_item_reviews ?? {}),
                [payload.audit_item_id]: payload.audit_item_review,
              },
              reviewed_count: payload.review_stats.reviewed_count,
              confirmed_count: payload.review_stats.confirmed_count,
              false_positive_count: payload.review_stats.false_positive_count,
              manual_review_count: payload.review_stats.manual_review_count,
              ignored_count: payload.review_stats.ignored_count,
            }
          : currentTask,
      );
    } catch (err) {
      setReviewError(err instanceof Error ? err.message : "复核提交失败。");
    } finally {
      setReviewSavingAuditItemId("");
    }
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

  async function handleDownloadReport() {
    const reportUrl = task?.report_url;
    if (!reportUrl || isReportDownloading) {
      return;
    }
    setIsReportDownloading(true);
    setReportDownloadError("");
    try {
      await downloadPdfFile(toApiUrl(reportUrl), task.report_filename || "合同差异分析报告.pdf");
    } catch (err) {
      setReportDownloadError(err instanceof Error ? err.message : "报告下载失败。");
    } finally {
      setIsReportDownloading(false);
    }
  }

  if (isLoading) {
    return <StateScreen title="正在载入审查结果" detail={`任务 ${taskId}`} />;
  }

  if (error || !task) {
    return <StateScreen title="无法打开审查结果" detail={error || "任务不存在。"} onBack={onBack} />;
  }

  if (task.status === "PROCESSING") {
    const progressPercent = Math.max(0, Math.min(100, task.progress_percent || 0));
    return (
      <section className="state-screen">
        <p className="eyebrow">合同审查系统</p>
        <h1>{task.stage || "处理中"}</h1>
        <div className="progress-ring-panel">
          <ProgressRing value={progressPercent} label={task.stage || "处理中"} size="large" />
        </div>
        <p>{`任务 ${taskId}`}</p>
      </section>
    );
  }

  if (task.status === "FAILED") {
    return (
      <StateScreen
        title="合同对比失败"
        detail={task.errors.length > 0 ? task.errors.join("；") : task.stage || "处理失败。"}
        onBack={onBack}
      />
    );
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

          <div className="pdf-bar-actions">
            <button
              className="report-export-button"
              type="button"
              disabled={!task.report_url || isReportDownloading}
              title={task.report_url ? "导出合同差异分析报告" : "报告尚未生成"}
              onClick={() => void handleDownloadReport()}
            >
              <Download aria-hidden="true" />
              {isReportDownloading ? "导出中..." : "导出报告"}
            </button>
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
          </div>
        </header>
        {reportDownloadError && (
          <p className="report-export-error" role="alert">
            {reportDownloadError}
          </p>
        )}

        <section
          className={isOriginalVisible ? "pdf-compare original-visible" : "pdf-compare original-hidden"}
          aria-label="左右合同 PDF 预览"
        >
          <PdfDocumentViewer
            ref={originalViewerRef}
            side="original"
            title="原版"
            src={toApiUrl(task.original_pdf_url)}
            diffs={diffs}
            zoom={zoom}
            activeDiffId={activeDiffId}
            syncEnabled={isSyncScroll}
            onScrollRatio={handleScrollRatio}
            onActivateDiff={focusDiff}
          />
          <div className="compare-axis" aria-label="差异比对轴">
            {axisMarkers.length === 0 ? (
              <>
                <i className="axis-marker add" />
                <i className="axis-marker modify" />
              </>
            ) : (
              axisMarkers.map((marker) => (
                <button
                  key={marker.id}
                  className={marker.id === activeAuditItemId ? `axis-marker ${marker.type.toLowerCase()} active` : `axis-marker ${marker.type.toLowerCase()}`}
                  type="button"
                  style={{ top: `${marker.positionPercent}%` }}
                  aria-label={`定位${diffTypeLabel(marker.type)}改动 ${marker.id}`}
                  onClick={() => focusDiff(marker.diffId, marker.id)}
                />
              ))
            )}
          </div>
          <PdfDocumentViewer
            ref={compareViewerRef}
            side="compare"
            title="新版"
            src={toApiUrl(task.compare_pdf_url)}
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
          reviewSavingAuditItemId={reviewSavingAuditItemId}
          reviewError={reviewError}
          stats={auditStats}
          onClose={() => setIsAuditPanelOpen(false)}
          onFilterChange={setDiffFilter}
          onSelectItem={focusAuditItem}
          onReview={handleReview}
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

interface AxisMarkerItem {
  id: string;
  diffId: string;
  type: DiffType;
  pageNo: number;
  y0: number;
  positionPercent: number;
}

interface AuditChangeItem {
  id: string;
  diffId: string;
  type: DiffType;
  title: string;
  summary: string;
  pageNo: number | null;
  y0: number | null;
  reviewStatus: ReviewStatus;
  reviewComment: string;
}

interface EvidenceLocation {
  pageNo: number;
  y0: number;
}

const ESTIMATED_PAGE_HEIGHT = 842;
const AXIS_MIN_TOP = 3;
const AXIS_MAX_TOP = 97;
const AXIS_MIN_GAP = 4;

function buildAxisMarkers(items: AuditChangeItem[]): AxisMarkerItem[] {
  const candidates = items
    .filter((item) => item.pageNo !== null && item.y0 !== null)
    .map((item) => ({
      id: item.id,
      diffId: item.diffId,
      type: item.type,
      pageNo: item.pageNo as number,
      y0: item.y0 as number,
    }))
    .sort((left, right) => {
      const positionDiff = left.pageNo - right.pageNo || left.y0 - right.y0;
      if (positionDiff !== 0) {
        return positionDiff;
      }
      return axisTypePriority(left.type) - axisTypePriority(right.type) || left.id.localeCompare(right.id);
    });
  if (candidates.length === 0) {
    return [];
  }

  const maxPageNo = Math.max(...candidates.map((candidate) => candidate.pageNo), 1);
  const documentHeight = maxPageNo * ESTIMATED_PAGE_HEIGHT;
  const markers = candidates.map((candidate) => {
    const documentY = (candidate.pageNo - 1) * ESTIMATED_PAGE_HEIGHT + candidate.y0;
    const rawPercent = documentHeight > 0 ? (documentY / documentHeight) * 100 : 50;
    return { ...candidate, positionPercent: clampAxisPercent(rawPercent) };
  });
  return enforceAxisSpacing(markers);
}

function axisTypePriority(type: DiffType): number {
  return { ADD: 1, MODIFY: 2, DELETE: 3 }[type];
}

function enforceAxisSpacing(markers: AxisMarkerItem[]): AxisMarkerItem[] {
  if (markers.length <= 1) {
    return markers.map((marker) => ({ ...marker, positionPercent: clampAxisPercent(marker.positionPercent) }));
  }

  const availableRange = AXIS_MAX_TOP - AXIS_MIN_TOP;
  const effectiveGap = Math.min(AXIS_MIN_GAP, availableRange / (markers.length - 1));
  const spaced: AxisMarkerItem[] = [];
  for (const marker of markers) {
    const previous = spaced.at(-1);
    const rawPosition = clampAxisPercent(marker.positionPercent);
    if (!previous) {
      spaced.push({ ...marker, positionPercent: Math.max(AXIS_MIN_TOP, rawPosition) });
    } else {
      spaced.push({
        ...marker,
        positionPercent: Math.max(rawPosition, previous.positionPercent + effectiveGap),
      });
    }
  }

  const lastIndex = spaced.length - 1;
  if (spaced[lastIndex].positionPercent > AXIS_MAX_TOP) {
    spaced[lastIndex] = { ...spaced[lastIndex], positionPercent: AXIS_MAX_TOP };
    for (let index = lastIndex - 1; index >= 0; index -= 1) {
      const next = spaced[index + 1];
      const current = spaced[index];
      spaced[index] = {
        ...current,
        positionPercent: Math.max(AXIS_MIN_TOP, Math.min(current.positionPercent, next.positionPercent - effectiveGap)),
      };
    }
  }

  return spaced.map((marker) => ({
    ...marker,
    positionPercent: Number(marker.positionPercent.toFixed(2)),
  }));
}

function clampAxisPercent(value: number): number {
  return Math.min(AXIS_MAX_TOP, Math.max(AXIS_MIN_TOP, Number(value.toFixed(2))));
}

function buildAuditItems(
  diffs: DiffItem[],
  auditItemReviews: NonNullable<CompareTask["audit_item_reviews"]>,
): AuditChangeItem[] {
  return diffs.flatMap((diff) => auditItemsForDiff(diff, auditItemReviews));
}

function auditItemsForDiff(
  diff: DiffItem,
  auditItemReviews: NonNullable<CompareTask["audit_item_reviews"]>,
): AuditChangeItem[] {
  const originalEvidence = diff.original_evidence ?? [];
  const compareEvidence = diff.compare_evidence ?? [];
  const hasTypedEvidence = [...originalEvidence, ...compareEvidence].some((evidence) => Boolean(evidence.highlight_type));

  if (!hasTypedEvidence) {
    return [];
  }

  const items: AuditChangeItem[] = [];
  const addEvidence = typedEvidence(compareEvidence, "ADD");
  const deleteEvidence = typedEvidence(originalEvidence, "DELETE");
  const originalModifyEvidence = typedEvidence(originalEvidence, "MODIFY");
  const compareModifyEvidence = typedEvidence(compareEvidence, "MODIFY");
  const addText = evidenceText(addEvidence);
  const deleteText = evidenceText(deleteEvidence);
  const originalModifyText = evidenceText(originalModifyEvidence);
  const compareModifyText = evidenceText(compareModifyEvidence);
  if (addEvidence.length > 0) {
    items.push(auditItem(diff, "ADD", addText, addEvidence, auditItemReviews));
  }
  if (deleteEvidence.length > 0) {
    items.push(auditItem(diff, "DELETE", deleteText, deleteEvidence, auditItemReviews));
  }
  if (originalModifyEvidence.length > 0 || compareModifyEvidence.length > 0) {
    items.push(
      auditItem(
        diff,
        "MODIFY",
        modifySummary(originalModifyText, compareModifyText),
        [...originalModifyEvidence, ...compareModifyEvidence],
        auditItemReviews,
      ),
    );
  }
  return items.length > 0
    ? items
    : [auditItem(diff, diff.diff_type, diffSummary(diff), [...originalEvidence, ...compareEvidence], auditItemReviews)];
}

function auditItem(
  diff: DiffItem,
  type: DiffType,
  summary: string,
  evidenceList: NonNullable<DiffItem["compare_evidence"]>,
  auditItemReviews: NonNullable<CompareTask["audit_item_reviews"]>,
): AuditChangeItem {
  const location = evidenceLocation(evidenceList);
  const id = `${diff.diff_id}:${type}`;
  const review = auditItemReviews[id];
  return {
    id,
    diffId: diff.diff_id,
    type,
    title: diff.title || diff.clause_no || diff.diff_id,
    summary: compactText(summary || diffSummary(diff)),
    pageNo: location?.pageNo ?? null,
    y0: location?.y0 ?? null,
    reviewStatus: review?.review_status ?? "UNREVIEWED",
    reviewComment: review?.review_comment ?? "",
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

function typedEvidence(evidenceList: NonNullable<DiffItem["compare_evidence"]>, type: DiffType): NonNullable<DiffItem["compare_evidence"]> {
  return evidenceList.filter((evidence) => evidence.highlight_type === type);
}

function evidenceText(evidenceList: NonNullable<DiffItem["compare_evidence"]>): string {
  return compactText(evidenceList.map((evidence) => evidence.text).join(" "));
}

function evidenceLocation(evidenceList: NonNullable<DiffItem["compare_evidence"]>): EvidenceLocation | null {
  const evidence = evidenceList
    .filter((item) => item.page_no && item.bbox)
    .sort((left, right) => left.page_no - right.page_no || left.bbox.y0 - right.bbox.y0)[0];
  if (!evidence) {
    return null;
  }
  return {
    pageNo: evidence.page_no,
    y0: evidence.bbox.y0,
  };
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
  reviewSavingAuditItemId,
  reviewError,
  stats,
  onClose,
  onFilterChange,
  onSelectItem,
  onReview,
}: {
  activeAuditItemId: string;
  filter: DiffFilter;
  items: AuditChangeItem[];
  isOpen: boolean;
  reviewSavingAuditItemId: string;
  reviewError: string;
  stats: DiffStats;
  onClose: () => void;
  onFilterChange: (filter: DiffFilter) => void;
  onSelectItem: (item: AuditChangeItem) => void;
  onReview: (item: AuditChangeItem, status: ReviewStatus) => Promise<void>;
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

      {reviewError && (
        <p className="review-error" role="alert">
          {reviewError}
        </p>
      )}

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
              isSaving={reviewSavingAuditItemId === item.id}
              onSelect={onSelectItem}
              onReview={onReview}
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
  isSaving,
  onSelect,
  onReview,
}: {
  active: boolean;
  item: AuditChangeItem;
  tabIndex: number | undefined;
  isSaving: boolean;
  onSelect: (item: AuditChangeItem) => void;
  onReview: (item: AuditChangeItem, status: ReviewStatus) => Promise<void>;
}) {
  const isIgnored = item.reviewStatus === "IGNORED";
  const reviewActionLabel = isIgnored ? "恢复" : "忽略";
  const reviewActionStatus: ReviewStatus = isIgnored ? "UNREVIEWED" : "IGNORED";
  const ReviewActionIcon = isIgnored ? RotateCcw : Ban;
  const cardClassName = ["audit-diff-card", active ? "active" : "", isIgnored ? "ignored" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <article className={cardClassName}>
      <button
        className="audit-diff-main"
        type="button"
        aria-label={`审计定位改动 ${item.id}`}
        tabIndex={tabIndex}
        onClick={() => onSelect(item)}
      >
        <span className={`audit-type-badge ${item.type.toLowerCase()}`}>{diffTypeLabel(item.type)}</span>
        <strong>{item.title}</strong>
        <span>{item.summary}</span>
      </button>
      <div className="review-action-row">
        <button
          className={isIgnored ? "review-action restore" : "review-action"}
          type="button"
          aria-label={`${reviewActionLabel} ${item.id}`}
          disabled={isSaving}
          tabIndex={tabIndex}
          onClick={() => void onReview(item, reviewActionStatus)}
        >
          <ReviewActionIcon aria-hidden="true" />
          <span>{isSaving ? "保存" : reviewActionLabel}</span>
        </button>
      </div>
    </article>
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
