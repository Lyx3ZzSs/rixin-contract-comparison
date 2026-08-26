import { useMemo, useRef, useState } from "react";
import { Ban, ChevronRight, Download, Eye, EyeOff, PanelRightOpen, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";

import { PdfDocumentViewer, type PdfDocumentViewerHandle } from "../components/PdfDocumentViewer";
import { ProgressRing } from "../components/ProgressRing";
import { toApiUrl, updateAuditItemReview } from "../lib/api";
import { downloadAuthenticatedFile } from "../lib/authFetch";
import { useTaskProgress } from "../lib/hooks";
import { canRetryTask, taskStatusLabel } from "../lib/taskStatus";
import type { AuditItem, DiffItem, DiffType, ReviewStatus } from "../types";

interface ResultPageProps {
  taskId: string;
  onBack: () => void;
  accessToken?: string;
}

export function ResultPage({ taskId, onBack, accessToken = "" }: ResultPageProps) {
  const { task, diffs, isLoading, error, isRetrying, retry, setTask } = useTaskProgress(taskId);
  const [isOriginalVisible, setIsOriginalVisible] = useState(true);
  const [isSyncScroll, setIsSyncScroll] = useState(true);
  const [isAuditPanelOpen, setIsAuditPanelOpen] = useState(false);
  const [isReportDownloading, setIsReportDownloading] = useState(false);
  const [reportDownloadError, setReportDownloadError] = useState("");
  const [diffFilter, setDiffFilter] = useState<DiffFilter>("ALL");
  const [zoom, setZoom] = useState(1);
  const [activeDiffId, setActiveDiffId] = useState("");
  const [activeAuditItemId, setActiveAuditItemId] = useState("");
  const [reviewSavingAuditItemIds, setReviewSavingAuditItemIds] = useState<Set<string>>(() => new Set());
  const [reviewError, setReviewError] = useState("");
  const originalViewerRef = useRef<PdfDocumentViewerHandle | null>(null);
  const compareViewerRef = useRef<PdfDocumentViewerHandle | null>(null);

  const auditItems = useMemo(
    () => (task?.audit_items ?? []).map(toAuditChangeItem),
    [task?.audit_items],
  );
  const axisMarkers = useMemo(() => buildAxisMarkers(auditItems), [auditItems]);
  const auditStats = useMemo(() => buildAuditStats(auditItems), [auditItems]);
  const filteredAuditItems = useMemo(
    () => sortAuditItems(diffFilter === "ALL" ? auditItems : auditItems.filter((item) => item.type === diffFilter)),
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
    const originalRatio = originalViewerRef.current?.scrollToDiff(diff) ?? null;
    const compareRatio = compareViewerRef.current?.scrollToDiff(diff) ?? null;
    if (originalRatio === null && compareRatio !== null) {
      originalViewerRef.current?.syncScrollFrom(compareRatio);
    } else if (compareRatio === null && originalRatio !== null) {
      compareViewerRef.current?.syncScrollFrom(originalRatio);
    }
  }

  function focusAuditItem(item: AuditChangeItem) {
    focusDiff(item.diffId, item.id);
  }

  async function handleReview(item: AuditChangeItem, status: ReviewStatus) {
    setReviewSavingAuditItemIds((current) => new Set(current).add(item.id));
    setReviewError("");
    try {
      const payload = await updateAuditItemReview(taskId, item.id, {
        review_status: status,
      });
      setTask((currentTask) => {
        if (!currentTask) {
          return currentTask;
        }
        const auditItems = (currentTask.audit_items ?? []).map((auditItem) =>
          auditItem.audit_item_id === payload.audit_item.audit_item_id ? payload.audit_item : auditItem
        );
        if (payload.report_revision < currentTask.report_revision) {
          return { ...currentTask, audit_items: auditItems };
        }
        return {
          ...currentTask,
          audit_items: auditItems,
          reviewed_count: payload.review_stats.reviewed_count,
          confirmed_count: payload.review_stats.confirmed_count,
          false_positive_count: payload.review_stats.false_positive_count,
          manual_review_count: payload.review_stats.manual_review_count,
          ignored_count: payload.review_stats.ignored_count,
          report_revision: payload.report_revision,
        };
      });
    } catch (err) {
      setReviewError(err instanceof Error ? err.message : "复核提交失败。");
    } finally {
      setReviewSavingAuditItemIds((current) => {
        const next = new Set(current);
        next.delete(item.id);
        return next;
      });
    }
  }

  async function handleDownloadReport() {
    const reportUrl = task?.report_url;
    if (!reportUrl || isReportDownloading) {
      return;
    }
    setIsReportDownloading(true);
    setReportDownloadError("");
    try {
      await downloadAuthenticatedFile(toApiUrl(reportUrl), task.report_filename || "合同差异分析报告.pdf");
    } catch (err) {
      setReportDownloadError(err instanceof Error ? err.message : "报告下载失败。");
    } finally {
      setIsReportDownloading(false);
    }
  }

  if (isLoading) {
    return <StateScreen title="正在载入审查结果" detail={`任务 ${taskId}`} />;
  }

  if (!task) {
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
    const retryable = canRetryTask(task.status, task.terminal_reason, task.retry_eligible);
    return (
      <section className="state-screen">
        <p className="eyebrow">合同审查系统</p>
        <h1>{taskStatusLabel(task.status, task.terminal_reason)}</h1>
        <p>{error || (task.errors.length > 0 ? task.errors.join("；") : task.stage || "处理失败。")}</p>
        {retryable && (
          <button className="primary-action" type="button" disabled={isRetrying} onClick={() => void retry()}>
            {isRetrying ? "重试中..." : "重试"}
          </button>
        )}
        <button type="button" onClick={onBack}>返回上传</button>
      </section>
    );
  }

  return (
    <section
      className="result-console"
      aria-labelledby="result-title"
      data-report-revision={task.report_revision}
      data-reviewed-count={task.reviewed_count ?? 0}
    >
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
                void downloadAuthenticatedFile(toApiUrl(task.original_pdf_url), task.original_filename || "原版文件.pdf").catch(console.error)
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
                  void downloadAuthenticatedFile(toApiUrl(task.compare_pdf_url), task.compare_filename || "新版文件.pdf").catch(console.error)
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
            accessToken={accessToken}
            diffs={diffs}
            recognitionOutlines={task.signing_region_outlines?.original ?? []}
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
            accessToken={accessToken}
            diffs={diffs}
            recognitionOutlines={task.signing_region_outlines?.compare ?? []}
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
          reviewSavingAuditItemIds={reviewSavingAuditItemIds}
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
  group: AuditGroup;
  title: string;
  summary: string;
  pageNo: number | null;
  y0: number | null;
  reviewStatus: ReviewStatus;
  reviewComment: string;
  qualityStatus: DiffItem["quality_status"];
  reviewFlags: string[];
  ocrBadge: { className: string; label: string } | null;
  remediationBadge: { className: string; label: string } | null;
}

interface EvidenceLocation {
  pageNo: number;
  y0: number;
}

type AuditGroup = "MAIN" | "SIGNING" | "STRUCTURAL" | "OTHER";

const auditGroupLabels: Record<AuditGroup, string> = {
  MAIN: "正文差异",
  SIGNING: "签章区差异",
  STRUCTURAL: "结构与质量提示",
  OTHER: "其他差异",
};

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

function toAuditChangeItem(
  item: AuditItem,
): AuditChangeItem {
  const location = evidenceLocation(item);
  return {
    id: item.audit_item_id,
    diffId: item.diff_id,
    type: item.diff_type,
    group: auditGroup(item),
    title: item.title || item.diff_id,
    summary: compactText(item.summary),
    pageNo: location?.pageNo ?? null,
    y0: location?.y0 ?? null,
    reviewStatus: item.review_status,
    reviewComment: item.review_comment,
    qualityStatus: item.quality_status,
    reviewFlags: item.review_flags,
    ocrBadge: item.ocr_context.affected ? { className: "needs-review", label: "OCR 质量风险" } : null,
    remediationBadge: remediationBadgeForItem(item),
  };
}

function sortAuditItems(items: AuditChangeItem[]): AuditChangeItem[] {
  return [...items].sort((left, right) => {
    const qualityDiff = auditQualityPriority(left) - auditQualityPriority(right);
    if (qualityDiff !== 0) {
      return qualityDiff;
    }
    return (left.pageNo ?? 9999) - (right.pageNo ?? 9999) || (left.y0 ?? 999999) - (right.y0 ?? 999999) || left.id.localeCompare(right.id);
  });
}

function auditQualityPriority(item: AuditChangeItem): number {
  if (item.group === "MAIN" && item.reviewFlags.includes("CRITICAL_VALUE_CHANGE")) {
    return 0;
  }
  if (item.group === "MAIN") {
    return 1;
  }
  if (item.group === "SIGNING") {
    return 2;
  }
  if (item.group === "STRUCTURAL") {
    return 3;
  }
  return 4;
}

function auditGroup(item: Pick<AuditItem, "source_type" | "section_type" | "review_flags">): AuditGroup {
  const flags = item.review_flags ?? [];
  if (item.source_type === "signing_region" || flags.some((flag) => flag.startsWith("SIGNING_"))) {
    return "SIGNING";
  }
  if (
    item.source_type === "header_footer"
    || item.source_type === "seal"
    || flags.includes("HEADER_FOOTER_REVIEW")
    || flags.includes("SEAL_REVIEW")
    || flags.includes("POSSIBLE_OCR_NOISE")
    || flags.includes("POSSIBLE_SPLIT_DRIFT")
    || flags.includes("READING_ORDER_REPAIRED")
  ) {
    return "STRUCTURAL";
  }
  if ((item.source_type ?? "clause") === "clause" && (!item.section_type || item.section_type === "main_contract")) {
    return "MAIN";
  }
  return "OTHER";
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

function groupedAuditItems(items: AuditChangeItem[]): Array<{ group: AuditGroup; items: AuditChangeItem[] }> {
  const groups: Array<{ group: AuditGroup; items: AuditChangeItem[] }> = [];
  const groupOrder: AuditGroup[] = ["MAIN", "SIGNING", "STRUCTURAL", "OTHER"];
  for (const group of groupOrder) {
    const groupItems = items.filter((item) => item.group === group);
    if (groupItems.length > 0) {
      groups.push({ group, items: groupItems });
    }
  }
  return groups;
}

function evidenceLocation(item: AuditItem): EvidenceLocation | null {
  if (item.evidence_state !== "LOCATED") {
    return null;
  }
  const evidence = [...item.original_evidence, ...item.compare_evidence]
    .filter((candidate) => {
      const { bbox } = candidate;
      return candidate.page_no > 0
        && [bbox.x0, bbox.y0, bbox.x1, bbox.y1].every(Number.isFinite)
        && bbox.x1 > bbox.x0
        && bbox.y1 > bbox.y0;
    })
    .sort((left, right) => left.page_no - right.page_no || left.bbox.y0 - right.bbox.y0)[0];
  if (!evidence) {
    return null;
  }
  return {
    pageNo: evidence.page_no,
    y0: evidence.bbox.y0,
  };
}

function AuditPanel({
  activeAuditItemId,
  filter,
  items,
  isOpen,
  reviewSavingAuditItemIds,
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
  reviewSavingAuditItemIds: Set<string>;
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
          groupedAuditItems(items).map((group) => (
            <section className="audit-diff-group" key={group.group} aria-label={auditGroupLabels[group.group]}>
              <div className="audit-diff-group-head">
                <strong>{auditGroupLabels[group.group]}</strong>
                <span>{group.items.length}</span>
              </div>
              {group.items.map((item) => (
                <AuditDiffCard
                  key={item.id}
                  active={item.id === activeAuditItemId}
                  item={item}
                  tabIndex={hiddenTabIndex}
                  isSaving={reviewSavingAuditItemIds.has(item.id)}
                  onSelect={onSelectItem}
                  onReview={onReview}
                />
              ))}
            </section>
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
  const qualityBadges = auditQualityBadges(item);
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
        <span className="audit-card-badges">
          <span className={`audit-type-badge ${item.type.toLowerCase()}`}>{diffTypeLabel(item.type)}</span>
          <span className={`audit-group-badge ${item.group.toLowerCase()}`}>{auditGroupLabels[item.group]}</span>
          <span className={`audit-review-badge ${item.reviewStatus.toLowerCase()}`}>
            {reviewStatusLabel(item.reviewStatus)}
          </span>
          {qualityBadges.map((badge) => (
            <span key={badge.label} className={`audit-quality-badge ${badge.className}`}>
              {badge.label}
            </span>
          ))}
          {item.ocrBadge ? (
            <span className={`audit-quality-badge ${item.ocrBadge.className}`}>{item.ocrBadge.label}</span>
          ) : null}
          {item.remediationBadge ? (
            <span className={`audit-quality-badge ${item.remediationBadge.className}`}>
              {item.remediationBadge.label}
            </span>
          ) : null}
        </span>
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

function reviewStatusLabel(status: ReviewStatus): string {
  return {
    UNREVIEWED: "未审核",
    CONFIRMED: "已确认",
    FALSE_POSITIVE: "误报",
    NEEDS_REVIEW: "需复核",
    IGNORED: "已忽略",
  }[status];
}

function auditQualityBadges(item: AuditChangeItem): Array<{ className: string; label: string }> {
  const badges: Array<{ className: string; label: string }> = [];
  if (item.reviewFlags.includes("CRITICAL_VALUE_CHANGE")) {
    badges.push({ className: "critical", label: "关键差异" });
  }
  if (item.qualityStatus === "NEEDS_REVIEW") {
    badges.push({ className: "needs-review", label: "待复核" });
  }
  if (item.reviewFlags.includes("EVIDENCE_UNLOCATED")) {
    badges.push({ className: "needs-review", label: "证据未定位" });
  }
  if (item.reviewFlags.includes("OCR_LOW_CONFIDENCE")) {
    badges.push({ className: "needs-review", label: "低置信 OCR" });
  }
  if (item.reviewFlags.includes("PAGE_UNRELIABLE")) {
    badges.push({ className: "needs-review", label: "页面不可靠" });
  }
  if (item.reviewFlags.includes("TABLE_STRUCTURE_UNRELIABLE")) {
    badges.push({ className: "needs-review", label: "表格识别风险" });
  }
  if (item.reviewFlags.includes("LAYOUT_MISMATCH_RISK")) {
    badges.push({ className: "needs-review", label: "版面匹配风险" });
  }
  if (item.reviewFlags.includes("READING_ORDER_RISK")) {
    badges.push({ className: "needs-review", label: "阅读顺序风险" });
  }
  if (item.reviewFlags.includes("SEAL_OR_SIGNATURE_RISK")) {
    badges.push({ className: "needs-review", label: "签章识别风险" });
  }
  if (item.reviewFlags.includes("EVIDENCE_UNRELIABLE")) {
    badges.push({ className: "needs-review", label: "证据不可靠" });
  }
  if (item.reviewFlags.includes("CROSS_SOURCE_MERGED")) {
    badges.push({ className: "merged", label: "已合并" });
  }
  return badges;
}

function remediationBadgeForItem(item: AuditItem): { className: string; label: string } | null {
  const statuses = item.remediation_context.statuses;
  if (statuses.length === 0) {
    return null;
  }
  if (item.remediation_context.requires_manual_review || statuses.includes("MANUAL_REVIEW_REQUIRED")) {
    return { className: "needs-review", label: "需人工处置" };
  }
  if (statuses.includes("FAILED")) {
    return { className: "needs-review", label: "处置未完成" };
  }
  if (statuses.includes("PLANNED")) {
    return { className: "needs-review", label: "处置规划" };
  }
  if (statuses.includes("SUCCEEDED")) {
    return { className: "merged", label: "已自动处置" };
  }
  return { className: "needs-review", label: "处置未完成" };
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
