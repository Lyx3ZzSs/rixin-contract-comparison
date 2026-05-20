import { ChangeEvent, useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.mjs?url";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist/types/src/pdf";

import { extractionFields } from "../data/extractionFields";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

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

  function toggleSemanticExtraction(fieldId: string) {
    setFields((currentFields) =>
      currentFields.map((field) =>
        field.id === fieldId ? { ...field, semanticExtraction: !field.semanticExtraction } : field,
      ),
    );
  }

  function removeField(fieldId: string) {
    setFields((currentFields) => currentFields.filter((field) => field.id !== fieldId));
  }

  return (
    <section className="extract-flow" aria-labelledby="extract-flow-title">
      <header className="extract-flow-header">
        <button className="extract-close-button" type="button" onClick={onBack} aria-label="返回上传文件">
          ×
        </button>
        <h1 id="extract-flow-title">合同提取</h1>
        <ol className="extract-steps" aria-label="合同提取步骤">
          <li className="done">
            <span>✓</span>
            选择合同
          </li>
          <li className="active">
            <span>2</span>
            设置提取字段
          </li>
          <li>
            <span>3</span>
            数据提取
          </li>
        </ol>
        <button className="extract-flow-submit" type="button">
          开始提取
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
                <span className="field-name" role="cell" title={field.name}>
                  {field.name}
                </span>
                <span className="field-type" role="cell">
                  {field.type}
                </span>
                <span className="field-description" role="cell" title={field.description}>
                  {field.description}
                </span>
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
                  <button type="button">编辑</button>
                  <button type="button" onClick={() => removeField(field.id)}>
                    移除
                  </button>
                </span>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </section>
  );
}

function FlatPdfPreview({ src, file }: { src: string; file: File }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [loadState, setLoadState] = useState<"idle" | "loading" | "ready" | "error">("idle");

  useEffect(() => {
    if (!src) {
      setPdf(null);
      setLoadState("idle");
      return undefined;
    }

    let isMounted = true;
    const loadingTask = pdfjsLib.getDocument(src);
    setLoadState("loading");
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

  if (!src || loadState === "idle") {
    return <DocumentFallback file={file} />;
  }

  return (
    <div className="extract-flat-pdf" aria-label="合同 PDF 平铺预览">
      {loadState === "loading" && <div className="extract-document-loading">正在载入 PDF...</div>}
      {loadState === "error" && <DocumentFallback file={file} />}
      {pdf &&
        Array.from({ length: pdf.numPages }, (_, index) => (
          <ExtractPdfPageCanvas key={`${src}-${index + 1}`} pdf={pdf} pageNumber={index + 1} zoom={0.78} />
        ))}
    </div>
  );
}

function ExtractPdfPageCanvas({ pdf, pageNumber, zoom }: { pdf: PDFDocumentProxy; pageNumber: number; zoom: number }) {
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
    <div className="extract-pdf-page-frame" style={{ width: pageSize.width || undefined, height: pageSize.height || undefined }}>
      <canvas ref={canvasRef} aria-label={`第 ${pageNumber} 页`} />
    </div>
  );
}

function DocumentFallback({ file }: { file: File }) {
  return (
    <div className="extract-document-fallback">
      <span aria-hidden="true" />
      <strong>{file.name}</strong>
      <p>合同文件已上传，正在准备预览。</p>
    </div>
  );
}
