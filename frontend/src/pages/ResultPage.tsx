import { useEffect, useMemo, useRef, useState } from "react";
import { Download, Eye, EyeOff, ZoomIn, ZoomOut } from "lucide-react";

import { PdfDocumentViewer, type PdfDocumentViewerHandle } from "../components/PdfDocumentViewer";
import { getDiffs, getTask, toApiUrl } from "../lib/api";
import type { CompareTask, DiffItem } from "../types";

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
  const [zoom, setZoom] = useState(1);
  const [activeDiffId, setActiveDiffId] = useState("");
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
      <section className="pdf-review-page" aria-label="PDF 在线对比预览">
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
