import { ChangeEvent, FormEvent, useMemo, useRef, useState } from "react";

import { compareContracts } from "../lib/api";

interface UploadPageProps {
  onTaskCreated: (taskId: string) => void;
}

export function UploadPage({ onTaskCreated }: UploadPageProps) {
  const [originalFile, setOriginalFile] = useState<File | null>(null);
  const [compareFile, setCompareFile] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const canSubmit = useMemo(
    () => Boolean(originalFile && compareFile && !isSubmitting),
    [compareFile, isSubmitting, originalFile],
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!originalFile || !compareFile) {
      setError("请先选择原版文件和新版文件。");
      return;
    }

    setIsSubmitting(true);
    setError("");
    setMessage("正在上传并执行合同差异审查...");
    try {
      const payload = await compareContracts(originalFile, compareFile);
      setMessage(`审查完成，识别 ${payload.diff_count} 项差异。`);
      onTaskCreated(payload.task_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "合同对比失败。");
      setMessage("处理未完成");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className="compare-workspace" aria-labelledby="upload-title">
      <header className="compare-hero">
        <div>
          <div className="hero-title-row">
            <h1 id="upload-title">智能合同对比</h1>
          </div>
          <span className="hero-shadow-text" aria-hidden="true">
            合同
          </span>
        </div>
        <div className="hero-docs" aria-hidden="true">
          <div className="mini-doc original">
            <strong>原版文件</strong>
            <span />
            <span />
            <span />
            <span />
          </div>
          <div className="doc-link-line" />
          <div className="mini-doc compare">
            <strong>新版文件</strong>
            <span />
            <span />
            <span />
            <span />
          </div>
        </div>
      </header>

      <form className="compare-card" onSubmit={handleSubmit}>
        <div className="compare-card-head">
          <h2>原版文件</h2>
          <span aria-hidden="true" />
          <h2>新版文件</h2>
        </div>

        <div className="compare-dropzone">
          <FileInput
            id="original-file"
            label="原版文件"
            helper="请选择作为审查基线的 PDF"
            file={originalFile}
            onChange={setOriginalFile}
          />
          <div className="center-guide" aria-live="polite">
            <p>{originalFile && compareFile ? "文件已就绪，可以开始对比" : "请选择原版文件和新版文件后开始对比"}</p>
            <span>{message}</span>
          </div>
          <FileInput
            id="compare-file"
            label="新版文件"
            helper="请选择需要对比的 PDF"
            file={compareFile}
            onChange={setCompareFile}
          />
        </div>

        <button className="compare-submit" type="submit" disabled={!canSubmit}>
          {isSubmitting ? "处理中..." : "开始对比"}
        </button>
        <div className="compare-actions" aria-label="辅助操作">
          <button type="button">使用示例</button>
        </div>
        <p className={error ? "status-line error" : "status-line"} role="status">
          {error || ""}
        </p>
      </form>
    </section>
  );
}

interface FileInputProps {
  id: string;
  label: string;
  helper: string;
  file: File | null;
  onChange: (file: File | null) => void;
}

function FileInput({ id, label, helper, file, onChange }: FileInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    onChange(event.target.files?.[0] ?? null);
  }

  function handleChooseFile() {
    inputRef.current?.click();
  }

  function handleRemoveFile() {
    if (inputRef.current) {
      inputRef.current.value = "";
    }
    onChange(null);
  }

  const input = (
    <input
      aria-label={label}
      id={id}
      ref={inputRef}
      type="file"
      accept="application/pdf,.pdf"
      onChange={handleFileChange}
    />
  );

  if (file) {
    return (
      <div className="compare-file-zone has-file">
        {input}
        <span className="file-success-mark" aria-hidden="true" />
        <span className="file-zone-title">{label}上传成功</span>
        <span className="file-upload-name">{file.name}</span>
        <div className="file-upload-actions">
          <button type="button" className="file-reupload-button" onClick={handleChooseFile} aria-label={`重新上传${label}`}>
            重新上传
          </button>
          <button type="button" className="file-remove-button" onClick={handleRemoveFile} aria-label={`删除${label}`}>
            删除
          </button>
        </div>
      </div>
    );
  }

  return (
    <label className="compare-file-zone" htmlFor={id}>
      <span className="file-ghost" aria-hidden="true" />
      <span className="file-zone-title">点击选择 PDF 文件</span>
      <span className="file-zone-helper">{helper}</span>
      {input}
    </label>
  );
}
