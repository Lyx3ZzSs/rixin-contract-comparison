import { forwardRef, type ChangeEvent, type UIEvent, useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from "lucide-react";
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.mjs?url";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist/types/src/pdf";

import { getCurrentPageFromScroll } from "../components/pdfPageScroll";
import { extractionFields, type ExtractionFieldDefinition } from "../data/extractionFields";
import { extractFields } from "../lib/api";
import type { ExtractionFieldValue } from "../types";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

const DEFAULT_PREVIEW_ZOOM = 1;
const PREVIEW_RENDER_SCALE = 0.9;
const MIN_PREVIEW_ZOOM = 0.8;
const MAX_PREVIEW_ZOOM = 1.8;
const PREVIEW_ZOOM_STEP = 0.1;

export function ExtractionPage() {
  const [file, setFile] = useState<File | null>(null);
  const [documentUrl, setDocumentUrl] = useState("");

  useEffect(() => {
    if (!file || typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
      setDocumentUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(file);
    setDocumentUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  function handleFilesChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0] ?? null;
    setFile(selectedFile);
  }

  if (file) {
    return <ExtractionFieldSetup file={file} documentUrl={documentUrl} onBack={() => setFile(null)} />;
  }

  return (
    <section className="extract-workspace" aria-labelledby="extract-title">
      <header className="extract-hero">
        <div className="extract-title-row">
          <h1 id="extract-title">AI智能合同提取工具</h1>
        </div>
        <div className="extract-hero-art" aria-hidden="true">
          <div className="extract-source-doc">
            <strong>XXX 合同</strong>
            <span />
            <span />
            <span />
            <span />
            <span />
          </div>
          <div className="extract-result-card">
            <strong>
              <i>AI</i>
              智能数据提取
            </strong>
            <span />
            <span />
            <span />
          </div>
          <div className="extract-arrow" />
        </div>
      </header>

      <form className="extract-card">
        <h2>上传文件</h2>
        <label className="extract-upload-zone" htmlFor="extract-files">
          <input
            id="extract-files"
            type="file"
            accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.bmp,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg,image/bmp"
            aria-label="上传合同提取文件"
            onChange={handleFilesChange}
          />
          <span className="extract-upload-icon" aria-hidden="true">
            <i>PDF</i>
            <i>W</i>
            <i>IMG</i>
          </span>
          <strong>拖拽文件上传或点击上传本地文件</strong>
          <small>支持格式 pdf/word/png/jpg/jpeg/bmp，图片5MB以内，其他文件60MB以内</small>
        </label>
      </form>
    </section>
  );
}

interface ExtractionFieldSetupProps {
  file: File;
  documentUrl: string;
  onBack: () => void;
}

function ExtractionFieldSetup({ file, documentUrl, onBack }: ExtractionFieldSetupProps) {
  const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  const [fields, setFields] = useState(extractionFields);
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractionError, setExtractionError] = useState("");
  const [results, setResults] = useState<ExtractionFieldValue[] | null>(null);
  const [currentStep, setCurrentStep] = useState<1 | 2 | 3>(2);
  const [editingFieldId, setEditingFieldId] = useState("");
  const [fieldDraft, setFieldDraft] = useState<Pick<ExtractionFieldDefinition, "name" | "type" | "description">>({
    name: "",
    type: "",
    description: "",
  });

  function toggleSemanticExtraction(fieldId: string) {
    setFields((currentFields) =>
      currentFields.map((field) =>
        field.id === fieldId ? { ...field, semanticExtraction: !field.semanticExtraction } : field,
      ),
    );
  }

  function removeField(fieldId: string) {
    setFields((currentFields) => currentFields.filter((field) => field.id !== fieldId));
    if (editingFieldId === fieldId) {
      cancelFieldEdit();
    }
  }

  function startFieldEdit(field: ExtractionFieldDefinition) {
    setEditingFieldId(field.id);
    setFieldDraft({
      name: field.name,
      type: field.type,
      description: field.description,
    });
  }

  function updateFieldDraft(key: keyof typeof fieldDraft, value: string) {
    setFieldDraft((currentDraft) => ({ ...currentDraft, [key]: value }));
  }

  function cancelFieldEdit() {
    setEditingFieldId("");
    setFieldDraft({ name: "", type: "", description: "" });
  }

  function saveFieldEdit() {
    if (!editingFieldId) {
      return;
    }
    const nextName = fieldDraft.name.trim();
    const nextType = fieldDraft.type.trim();
    const nextDescription = fieldDraft.description.trim();
    if (!nextName || !nextType) {
      return;
    }
    setFields((currentFields) =>
      currentFields.map((field) =>
        field.id === editingFieldId
          ? {
              ...field,
              name: nextName,
              type: nextType,
              description: nextDescription,
            }
          : field,
      ),
    );
    cancelFieldEdit();
  }

  async function handleStartExtraction() {
    if (fields.length === 0) return;

    setIsExtracting(true);
    setExtractionError("");
    setCurrentStep(3);

    try {
      const response = await extractFields(file, fields);
      setResults(response.results);
      if (response.errors.length > 0) {
        setExtractionError(response.errors.join("; "));
      }
    } catch (err) {
      setExtractionError(err instanceof Error ? err.message : "提取失败，请重试");
      setCurrentStep(2);
    } finally {
      setIsExtracting(false);
    }
  }

  function handleExport() {
    if (!results) return;
    const header = "字段名称,提取值,状态\n";
    const rows = results
      .map(
        (r) =>
          `"${r.field_name}","${r.value || ""}","${r.status === "found" ? "已找到" : r.status === "not_found" ? "未找到" : "错误"}"`,
      )
      .join("\n");
    const bom = "﻿";
    const blob = new Blob([bom + header + rows], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${file.name.replace(/\.[^.]+$/, "")}_提取结果.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const activeFieldCount = fields.length;

  return (
    <section className="extract-flow" aria-labelledby="extract-flow-title">
      <header className="extract-flow-header">
        <button className="extract-close-button" type="button" onClick={onBack} aria-label="返回上传文件">
          ×
        </button>
        <div className="extract-flow-center">
          <h1 id="extract-flow-title">合同提取</h1>
          <ol className="extract-steps" aria-label="合同提取步骤">
            <li className="done">
              <span>✓</span>
              选择合同
            </li>
            <li className={currentStep >= 2 ? (results ? "done" : "active") : ""}>
              <span>{currentStep >= 3 && results ? "✓" : "2"}</span>
              设置提取字段
            </li>
            <li className={currentStep >= 3 ? "active" : ""}>
              <span>3</span>
              数据提取
            </li>
          </ol>
        </div>
        <button
          className="extract-flow-submit"
          type="button"
          onClick={results ? handleExport : handleStartExtraction}
          disabled={isExtracting || (!results && activeFieldCount === 0)}
        >
          {isExtracting ? "正在提取..." : results ? "导出" : "开始提取"}
        </button>
      </header>

      <div className="extract-flow-body">
        <aside className="extract-file-list" aria-label="合同文件列表">
          <label className="extract-search">
            <span>请输入</span>
            <input aria-label="搜索合同文件" />
          </label>
          <button className="active" type="button" title={file.name}>
            <span>1.</span>
            <strong>{file.name}</strong>
          </button>
        </aside>

        <main className="extract-preview" aria-label="合同预览">
          <div className="extract-document-page">
            {isPdf ? (
              <FlatPdfPreview src={documentUrl} file={file} />
            ) : (
              <DocumentFallback file={file} />
            )}
          </div>
        </main>

        <aside className="extract-field-panel" aria-label="字段列表">
          {isExtracting && !results ? (
            <div className="extract-card-view">
              <div className="extract-card-header">
                <h2>提取字段信息</h2>
              </div>
              <div className="extract-status-bar">
                <span className="extract-status-spinner" aria-hidden="true" />
                正在提取合同的结构信息
              </div>
              <div className="extract-card-list">
                {fields.map((field) => (
                  <div className="extract-card-item" key={field.id}>
                    <span className="extract-card-name">{field.name}</span>
                    <span className="extract-card-value loading">提取中...</span>
                  </div>
                ))}
              </div>
            </div>
          ) : results ? (
            <div className="extract-card-view">
              <div className="extract-card-header">
                <h2>提取字段信息</h2>
                <button type="button" className="extract-back-link" onClick={() => { setResults(null); setCurrentStep(2); }}>
                  返回字段设置
                </button>
              </div>
              {extractionError && (
                <div className="extract-error-banner" role="alert">{extractionError}</div>
              )}
              <div className="extract-card-list">
                {results.map((r) => (
                  <div className="extract-card-item" key={r.field_id}>
                    <span className="extract-card-name">{r.field_name}</span>
                    <span className={`extract-card-value${r.status === "not_found" ? " empty" : r.status === "error" ? " error" : ""}`}>
                      {r.status === "found" ? (r.value || "—") : r.status === "not_found" ? "未找到" : "提取失败"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <>
              <div className="extract-field-panel-head">
                <h2>字段列表</h2>
                <div className="extract-field-actions" aria-label="字段操作">
                  <button type="button">+ 自定义添加</button>
                  <button type="button">从字段库/模板添加</button>
                  <button type="button">从Excel导入</button>
                </div>
              </div>
              <div className="extract-field-grid" role="table" aria-label="提取字段列表">
                <div className="extract-field-row header" role="row">
                  <strong role="columnheader">字段名称</strong>
                  <strong role="columnheader">字段类型</strong>
                  <strong role="columnheader">字段描述</strong>
                  <strong role="columnheader">语义提取</strong>
                  <strong role="columnheader">操作</strong>
                </div>
                {fields.map((field) => (
                  <div className="extract-field-row" role="row" key={field.id}>
                    {editingFieldId === field.id ? (
                      <>
                        <span className="field-name editing" role="cell">
                          <input
                            className="field-edit-input"
                            aria-label="编辑字段名称"
                            value={fieldDraft.name}
                            onChange={(event) => updateFieldDraft("name", event.target.value)}
                          />
                        </span>
                        <span className="field-type editing" role="cell">
                          <input
                            className="field-edit-input"
                            aria-label="编辑字段类型"
                            value={fieldDraft.type}
                            onChange={(event) => updateFieldDraft("type", event.target.value)}
                          />
                        </span>
                        <span className="field-description editing" role="cell">
                          <input
                            className="field-edit-input"
                            aria-label="编辑字段描述"
                            value={fieldDraft.description}
                            onChange={(event) => updateFieldDraft("description", event.target.value)}
                          />
                        </span>
                      </>
                    ) : (
                      <>
                        <span className="field-name" role="cell" title={field.name}>
                          {field.name}
                        </span>
                        <span className="field-type" role="cell">
                          {field.type}
                        </span>
                        <span className="field-description" role="cell" title={field.description}>
                          {field.description}
                        </span>
                      </>
                    )}
                    <span className="field-semantic" role="cell">
                      <button
                        className={field.semanticExtraction ? "field-switch on" : "field-switch"}
                        type="button"
                        aria-label={`${field.name}语义提取`}
                        aria-pressed={field.semanticExtraction}
                        onClick={() => toggleSemanticExtraction(field.id)}
                      />
                    </span>
                    <span role="cell" className="field-actions">
                      {editingFieldId === field.id ? (
                        <>
                          <button type="button" onClick={saveFieldEdit} disabled={!fieldDraft.name.trim() || !fieldDraft.type.trim()}>
                            保存
                          </button>
                          <button type="button" onClick={cancelFieldEdit}>
                            取消
                          </button>
                        </>
                      ) : (
                        <>
                          <button type="button" onClick={() => startFieldEdit(field)}>
                            编辑
                          </button>
                          <button type="button" onClick={() => removeField(field.id)}>
                            移除
                          </button>
                        </>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}


function FlatPdfPreview({ src, file }: { src: string; file: File }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [loadState, setLoadState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(DEFAULT_PREVIEW_ZOOM);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pageRefs = useRef(new Map<number, HTMLDivElement>());

  useEffect(() => {
    if (!src) {
      setPdf(null);
      setLoadState("idle");
      setCurrentPage(1);
      setZoom(DEFAULT_PREVIEW_ZOOM);
      return undefined;
    }

    let isMounted = true;
    const loadingTask = pdfjsLib.getDocument(src);
    setLoadState("loading");
    setCurrentPage(1);
    setZoom(DEFAULT_PREVIEW_ZOOM);
    loadingTask.promise
      .then((document) => {
        if (!isMounted) {
          document.destroy();
          return;
        }
        setPdf(document);
        setLoadState("ready");
      })
      .catch(() => {
        if (isMounted) {
          setPdf(null);
          setLoadState("error");
        }
      });

    return () => {
      isMounted = false;
      loadingTask.destroy();
    };
  }, [src]);

  const updateCurrentPageFromScroll = useCallback(() => {
    const scrollNode = scrollRef.current;
    if (!scrollNode) {
      return;
    }
    const pages = Array.from(pageRefs.current.entries())
      .map(([pageNumber, node]) => ({
        pageNumber,
        offsetTop: node.offsetTop,
        offsetHeight: node.offsetHeight,
      }))
      .sort((left, right) => left.pageNumber - right.pageNumber);
    setCurrentPage(getCurrentPageFromScroll(scrollNode.scrollTop, scrollNode.clientHeight, pages));
  }, []);

  useEffect(() => {
    const frameId = window.requestAnimationFrame(updateCurrentPageFromScroll);
    return () => window.cancelAnimationFrame(frameId);
  }, [pdf, updateCurrentPageFromScroll, zoom]);

  function scrollToPage(pageNumber: number) {
    const totalPages = pdf?.numPages ?? 1;
    const targetPage = Math.min(totalPages, Math.max(1, pageNumber));
    const scrollNode = scrollRef.current;
    const pageNode = pageRefs.current.get(targetPage);
    if (scrollNode && pageNode) {
      scrollNode.scrollTo({ top: Math.max(0, pageNode.offsetTop - 16), behavior: "smooth" });
    }
    setCurrentPage(targetPage);
  }

  function goToPreviousPage() {
    scrollToPage(currentPage - 1);
  }

  function goToNextPage() {
    scrollToPage(currentPage + 1);
  }

  function handlePageChange(event: ChangeEvent<HTMLInputElement>) {
    const nextPage = Number(event.target.value);
    if (!Number.isFinite(nextPage)) {
      return;
    }
    scrollToPage(nextPage);
  }

  function zoomOut() {
    setZoom((value) => Math.max(MIN_PREVIEW_ZOOM, Number((value - PREVIEW_ZOOM_STEP).toFixed(2))));
  }

  function zoomIn() {
    setZoom((value) => Math.min(MAX_PREVIEW_ZOOM, Number((value + PREVIEW_ZOOM_STEP).toFixed(2))));
  }

  function handleScroll(_event: UIEvent<HTMLDivElement>) {
    updateCurrentPageFromScroll();
  }

  if (!src || loadState === "idle") {
    return <DocumentFallback file={file} />;
  }

  const totalPages = pdf?.numPages ?? 0;
  const renderedZoom = PREVIEW_RENDER_SCALE * zoom;

  return (
    <div className="extract-flat-pdf" aria-label="合同 PDF 预览">
      {loadState === "loading" && <div className="extract-document-loading">正在载入 PDF...</div>}
      {loadState === "error" && <DocumentFallback file={file} />}
      {pdf && (
        <>
          <div ref={scrollRef} className="extract-pdf-scroll-shell" onScroll={handleScroll}>
            {Array.from({ length: pdf.numPages }, (_, index) => (
              <ExtractPdfPageCanvas
                key={`${src}-${index + 1}-${renderedZoom}`}
                ref={(node) => {
                  if (node) {
                    pageRefs.current.set(index + 1, node);
                  } else {
                    pageRefs.current.delete(index + 1);
                  }
                }}
                pdf={pdf}
                pageNumber={index + 1}
                zoom={renderedZoom}
              />
            ))}
          </div>
          <div className="extract-pdf-toolbar" aria-label="PDF预览工具栏">
            <button type="button" onClick={goToPreviousPage} disabled={currentPage <= 1} aria-label="上一页" title="上一页">
              <ChevronLeft aria-hidden="true" />
            </button>
            <label className="extract-page-control">
              <input
                aria-label="当前页码"
                type="number"
                min={1}
                max={totalPages}
                value={currentPage}
                onChange={handlePageChange}
              />
              <span>/ {totalPages}</span>
            </label>
            <button type="button" onClick={goToNextPage} disabled={currentPage >= totalPages} aria-label="下一页" title="下一页">
              <ChevronRight aria-hidden="true" />
            </button>
            <button type="button" onClick={zoomOut} disabled={zoom <= MIN_PREVIEW_ZOOM} aria-label="缩小PDF" title="缩小">
              <ZoomOut aria-hidden="true" />
            </button>
            <output className="extract-zoom-value" aria-label="当前缩放比例">
              {Math.round(zoom * 100)}%
            </output>
            <button type="button" onClick={zoomIn} disabled={zoom >= MAX_PREVIEW_ZOOM} aria-label="放大PDF" title="放大">
              <ZoomIn aria-hidden="true" />
            </button>
          </div>
        </>
      )}
    </div>
  );
}

const ExtractPdfPageCanvas = forwardRef<
  HTMLDivElement,
  { pdf: PDFDocumentProxy; pageNumber: number; zoom: number }
>(function ExtractPdfPageCanvas({ pdf, pageNumber, zoom }, ref) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [pageSize, setPageSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    let isMounted = true;
    let renderTask: RenderTask | null = null;

    pdf.getPage(pageNumber).then((page) => {
      if (!isMounted) {
        return;
      }
      const viewport = page.getViewport({ scale: zoom });
      const canvas = canvasRef.current;
      const context = canvas?.getContext("2d");
      if (!canvas || !context) {
        return;
      }
      const outputScale = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      setPageSize({ width: viewport.width, height: viewport.height });
      const task = page.render({
        canvas,
        canvasContext: context,
        viewport,
        transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
      });
      renderTask = task;
      task.promise.catch(() => {
        // Rendering can be cancelled while replacing uploaded files.
      });
    });

    return () => {
      isMounted = false;
      renderTask?.cancel();
    };
  }, [pageNumber, pdf, zoom]);

  return (
    <div
      ref={ref}
      className="extract-pdf-page-frame"
      style={{ width: pageSize.width || undefined, height: pageSize.height || undefined }}
      data-page-number={pageNumber}
    >
      <canvas ref={canvasRef} aria-label={`第 ${pageNumber} 页`} />
    </div>
  );
});

function DocumentFallback({ file }: { file: File }) {
  return (
    <div className="extract-document-fallback">
      <span aria-hidden="true" />
      <strong>{file.name}</strong>
      <p>合同文件已上传，正在准备预览。</p>
    </div>
  );
}
