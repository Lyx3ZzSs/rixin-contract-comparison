import { useCallback, useEffect, useRef, useState } from "react";

import {
  createQualityExpectedDiff,
  evaluateQuality,
  getQualityCase,
  getQualityTaskReview,
  listQualityCases,
  runQualityRegression,
  updateQualityExpectedDiff,
} from "../lib/api";
import type {
  ExpectedDiff,
  QualityActualDiffSummary,
  QualityCaseDetail,
  QualityCaseSummary,
  QualityTaskReviewDiff,
  QualityTaskReviewResponse,
  QualityTaskSuppressedDiff,
  QualityRunResponse,
} from "../types";

export function QualityWorkbenchPage() {
  const [cases, setCases] = useState<QualityCaseSummary[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState("");
  const [detail, setDetail] = useState<QualityCaseDetail | null>(null);
  const [isLoadingCases, setIsLoadingCases] = useState(true);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [isAddingMissedDiff, setIsAddingMissedDiff] = useState(false);
  const [isSavingMissedDiff, setIsSavingMissedDiff] = useState(false);
  const [missedTitle, setMissedTitle] = useState("");
  const [editingEvidenceIndex, setEditingEvidenceIndex] = useState<number | null>(null);
  const [evidenceJson, setEvidenceJson] = useState("");
  const [evidenceError, setEvidenceError] = useState("");
  const [isSavingEvidence, setIsSavingEvidence] = useState(false);
  const [runResult, setRunResult] = useState<QualityRunResponse | null>(null);
  const [isRunningQuality, setIsRunningQuality] = useState(false);
  const [taskReviewId, setTaskReviewId] = useState("");
  const [taskReview, setTaskReview] = useState<QualityTaskReviewResponse | null>(null);
  const [taskReviewError, setTaskReviewError] = useState("");
  const [isLoadingTaskReview, setIsLoadingTaskReview] = useState(false);
  const [error, setError] = useState("");
  const detailRequestIdRef = useRef(0);
  const selectedCaseIdRef = useRef("");
  const expectedDiffMutationRequestIdRef = useRef(0);
  const missedDiffRequestInFlightRef = useRef(false);
  const missedDiffRequestIdRef = useRef(0);
  const evidenceRequestIdRef = useRef(0);
  const qualityRunRequestIdRef = useRef(0);
  const taskReviewRequestIdRef = useRef(0);

  const applyCaseDetail = useCallback((nextDetail: QualityCaseDetail) => {
    setDetail(nextDetail);
    setCases((currentCases) =>
      currentCases.map((qualityCase) =>
        qualityCase.case_id === nextDetail.summary.case_id ? nextDetail.summary : qualityCase,
      ),
    );
  }, []);

  const applyCaseDetailIfCurrent = useCallback(
    (requestCaseId: string, nextDetail: QualityCaseDetail) => {
      if (selectedCaseIdRef.current !== requestCaseId) return;
      if (nextDetail.summary.case_id !== requestCaseId) return;
      applyCaseDetail(nextDetail);
    },
    [applyCaseDetail],
  );

  const openCase = useCallback((caseId: string) => {
    const requestId = detailRequestIdRef.current + 1;
    detailRequestIdRef.current = requestId;
    selectedCaseIdRef.current = caseId;
    setSelectedCaseId(caseId);
    setDetail(null);
    setIsLoadingDetail(true);
    setIsAddingMissedDiff(false);
    setIsSavingMissedDiff(false);
    expectedDiffMutationRequestIdRef.current += 1;
    missedDiffRequestInFlightRef.current = false;
    missedDiffRequestIdRef.current += 1;
    evidenceRequestIdRef.current += 1;
    qualityRunRequestIdRef.current += 1;
    setMissedTitle("");
    setEditingEvidenceIndex(null);
    setEvidenceJson("");
    setEvidenceError("");
    setIsSavingEvidence(false);
    setRunResult(null);
    setIsRunningQuality(false);
    setError("");

    void getQualityCase(caseId)
      .then((payload) => {
        if (detailRequestIdRef.current === requestId) {
          applyCaseDetail(payload);
        }
      })
      .catch((err) => {
        if (detailRequestIdRef.current === requestId) {
          setError(err instanceof Error ? err.message : "质量样本详情加载失败。");
        }
      })
      .finally(() => {
        if (detailRequestIdRef.current === requestId) {
          setIsLoadingDetail(false);
        }
      });
  }, [applyCaseDetail]);

  useEffect(() => {
    let isCurrent = true;
    setIsLoadingCases(true);
    setError("");

    void listQualityCases()
      .then((payload) => {
        if (!isCurrent) return;
        setCases(payload.cases);
        if (payload.cases.length > 0) {
          openCase(payload.cases[0].case_id);
        }
      })
      .catch((err) => {
        if (isCurrent) {
          setCases([]);
          setDetail(null);
          setError(err instanceof Error ? err.message : "质量样本列表加载失败。");
        }
      })
      .finally(() => {
        if (isCurrent) setIsLoadingCases(false);
      });

    return () => {
      isCurrent = false;
      detailRequestIdRef.current += 1;
    };
  }, [openCase]);

  const markExpectedDiff = useCallback(
    async (index: number, payload: Partial<ExpectedDiff>) => {
      if (!detail) return;
      const requestCaseId = detail.summary.case_id;
      const mutationRequestId = expectedDiffMutationRequestIdRef.current + 1;
      expectedDiffMutationRequestIdRef.current = mutationRequestId;
      setError("");
      try {
        const nextDetail = await updateQualityExpectedDiff(requestCaseId, index, payload);
        if (expectedDiffMutationRequestIdRef.current === mutationRequestId) {
          applyCaseDetailIfCurrent(requestCaseId, nextDetail);
        }
      } catch (err) {
        if (expectedDiffMutationRequestIdRef.current === mutationRequestId && selectedCaseIdRef.current === requestCaseId) {
          setError(err instanceof Error ? err.message : "期望差异更新失败。");
        }
      }
    },
    [applyCaseDetailIfCurrent, detail],
  );

  const markFalsePositive = useCallback(
    (index: number) =>
      markExpectedDiff(index, {
        review_status: "REJECTED",
        should_not_match_again: true,
        false_positive_reason: "manual_false_positive",
      }),
    [markExpectedDiff],
  );

  const saveMissedDiff = useCallback(async () => {
    if (!detail) return;
    if (missedDiffRequestInFlightRef.current) return;
    const title = missedTitle.trim();
    if (!title) return;

    const requestCaseId = detail.summary.case_id;
    const saveRequestId = missedDiffRequestIdRef.current + 1;
    missedDiffRequestIdRef.current = saveRequestId;
    missedDiffRequestInFlightRef.current = true;
    setIsSavingMissedDiff(true);
    setError("");
    try {
      const nextDetail = await createQualityExpectedDiff(requestCaseId, {
        review_status: "APPROVED",
        diff_type: "MODIFY",
        source_type: "metadata",
        title_contains: title,
        false_negative_reason: "manual_missing_diff",
      });
      if (
        missedDiffRequestIdRef.current === saveRequestId &&
        selectedCaseIdRef.current === requestCaseId &&
        nextDetail.summary.case_id === requestCaseId
      ) {
        applyCaseDetail(nextDetail);
        setMissedTitle("");
        setIsAddingMissedDiff(false);
      }
    } catch (err) {
      if (missedDiffRequestIdRef.current === saveRequestId && selectedCaseIdRef.current === requestCaseId) {
        setError(err instanceof Error ? err.message : "漏报补录失败。");
      }
    } finally {
      if (missedDiffRequestIdRef.current === saveRequestId && selectedCaseIdRef.current === requestCaseId) {
        missedDiffRequestInFlightRef.current = false;
        setIsSavingMissedDiff(false);
      }
    }
  }, [applyCaseDetail, detail, missedTitle]);

  const openEvidenceEditor = useCallback((index: number, diff: ExpectedDiff) => {
    setEditingEvidenceIndex(index);
    setEvidenceJson(JSON.stringify(diff.expected_evidence ?? [], null, 2));
    setEvidenceError("");
  }, []);

  const saveEvidence = useCallback(async () => {
    if (!detail || editingEvidenceIndex === null) return;

    let parsed: unknown;
    try {
      parsed = JSON.parse(evidenceJson);
    } catch {
      setEvidenceError("证据 JSON 格式无效。");
      return;
    }

    if (!Array.isArray(parsed)) {
      setEvidenceError("证据 JSON 必须是数组。");
      return;
    }

    const requestCaseId = detail.summary.case_id;
    const requestIndex = editingEvidenceIndex;
    const saveRequestId = evidenceRequestIdRef.current + 1;
    evidenceRequestIdRef.current = saveRequestId;
    setIsSavingEvidence(true);
    setEvidenceError("");
    setError("");
    try {
      const nextDetail = await updateQualityExpectedDiff(requestCaseId, requestIndex, { expected_evidence: parsed });
      if (
        evidenceRequestIdRef.current === saveRequestId &&
        selectedCaseIdRef.current === requestCaseId &&
        nextDetail.summary.case_id === requestCaseId
      ) {
        applyCaseDetail(nextDetail);
        setEditingEvidenceIndex(null);
        setEvidenceJson("");
      }
    } catch (err) {
      if (evidenceRequestIdRef.current === saveRequestId && selectedCaseIdRef.current === requestCaseId) {
        setEvidenceError(err instanceof Error ? err.message : "证据保存失败。");
      }
    } finally {
      if (evidenceRequestIdRef.current === saveRequestId && selectedCaseIdRef.current === requestCaseId) {
        setIsSavingEvidence(false);
      }
    }
  }, [applyCaseDetail, detail, editingEvidenceIndex, evidenceJson]);

  const runQualityEvaluation = useCallback(async () => {
    const requestId = qualityRunRequestIdRef.current + 1;
    qualityRunRequestIdRef.current = requestId;
    setIsRunningQuality(true);
    setRunResult(null);
    setError("");
    try {
      const result = await evaluateQuality({ dataset_splits: ["regression"], run_id: "ui-eval" });
      if (qualityRunRequestIdRef.current === requestId) {
        setRunResult(result);
      }
    } catch (err) {
      if (qualityRunRequestIdRef.current === requestId) {
        setError(err instanceof Error ? err.message : "质量评估运行失败。");
      }
    } finally {
      if (qualityRunRequestIdRef.current === requestId) {
        setIsRunningQuality(false);
      }
    }
  }, []);

  const runQualityRegressionCheck = useCallback(async () => {
    const requestId = qualityRunRequestIdRef.current + 1;
    qualityRunRequestIdRef.current = requestId;
    setIsRunningQuality(true);
    setRunResult(null);
    setError("");
    try {
      const result = await runQualityRegression({
        dataset_splits: ["regression"],
        baseline_name: "current",
        run_id: "ui-regression",
      });
      if (qualityRunRequestIdRef.current === requestId) {
        setRunResult(result);
      }
    } catch (err) {
      if (qualityRunRequestIdRef.current === requestId) {
        setError(err instanceof Error ? err.message : "质量回归运行失败。");
      }
    } finally {
      if (qualityRunRequestIdRef.current === requestId) {
        setIsRunningQuality(false);
      }
    }
  }, []);

  const loadTaskReview = useCallback(async () => {
    const taskId = taskReviewId.trim();
    if (!taskId) return;

    const requestId = taskReviewRequestIdRef.current + 1;
    taskReviewRequestIdRef.current = requestId;
    setIsLoadingTaskReview(true);
    setTaskReviewError("");
    setTaskReview(null);
    try {
      const result = await getQualityTaskReview(taskId);
      if (taskReviewRequestIdRef.current === requestId) {
        setTaskReview(result);
      }
    } catch (err) {
      if (taskReviewRequestIdRef.current === requestId) {
        setTaskReviewError(err instanceof Error ? err.message : "任务复盘加载失败。");
      }
    } finally {
      if (taskReviewRequestIdRef.current === requestId) {
        setIsLoadingTaskReview(false);
      }
    }
  }, [taskReviewId]);

  const expectedDiffs = detail?.expected.expected_diffs ?? [];

  return (
    <section className="quality-workbench" aria-labelledby="quality-workbench-title">
      <header className="quality-workbench-header">
        <div>
          <span>Gold Case Review</span>
          <h1 id="quality-workbench-title">质量回归工作台</h1>
        </div>
        <div aria-label="质量样本统计">
          <strong>{cases.length}</strong>
          <span>cases</span>
        </div>
      </header>

      <div className="quality-workbench-layout">
        <aside className="quality-case-list" aria-label="质量样本列表">
          <div className="quality-section">
            <h2>Gold cases</h2>
            {isLoadingCases ? (
              <div className="quality-state" role="status">
                正在加载质量样本...
              </div>
            ) : cases.length === 0 && !error ? (
              <div className="quality-state">暂无质量样本</div>
            ) : (
              <div>
                {cases.map((qualityCase) => (
                  <button
                    key={qualityCase.case_id}
                    className={qualityCase.case_id === selectedCaseId ? "active" : ""}
                    type="button"
                    onClick={() => openCase(qualityCase.case_id)}
                    aria-current={qualityCase.case_id === selectedCaseId ? "true" : undefined}
                  >
                    <strong>{qualityCase.case_id}</strong>
                    <span>{qualityCase.dataset_split}</span>
                    <small>
                      {qualityCase.actual_diff_count} actual / {qualityCase.approved_expected_count} expected
                    </small>
                  </button>
                ))}
              </div>
            )}
          </div>
        </aside>

        <main className="quality-case-detail">
          <TaskReviewPanel
            taskReviewId={taskReviewId}
            taskReview={taskReview}
            taskReviewError={taskReviewError}
            isLoadingTaskReview={isLoadingTaskReview}
            onChangeTaskReviewId={setTaskReviewId}
            onLoadTaskReview={loadTaskReview}
          />
          {error ? (
            <div className="quality-state error" role="alert">
              <strong>质量工作台加载失败</strong>
              <span>{error}</span>
            </div>
          ) : isLoadingDetail ? (
            <div className="quality-state" role="status">
              正在加载质量样本详情...
            </div>
          ) : detail ? (
            <>
              <CaseSummary summary={detail.summary} />
              <QualityRunBar
                isRunningQuality={isRunningQuality}
                runResult={runResult}
                onRunEvaluation={runQualityEvaluation}
                onRunRegression={runQualityRegressionCheck}
              />
              <ActualDiffList diffs={detail.actual_diffs} />
              <ExpectedDiffList
                diffs={expectedDiffs}
                editingEvidenceIndex={editingEvidenceIndex}
                evidenceError={evidenceError}
                evidenceJson={evidenceJson}
                isAddingMissedDiff={isAddingMissedDiff}
                isSavingEvidence={isSavingEvidence}
                isSavingMissedDiff={isSavingMissedDiff}
                missedTitle={missedTitle}
                onChangeMissedTitle={setMissedTitle}
                onChangeEvidenceJson={setEvidenceJson}
                onOpenEvidenceEditor={openEvidenceEditor}
                onMarkExpectedDiff={markExpectedDiff}
                onMarkFalsePositive={markFalsePositive}
                onSaveEvidence={saveEvidence}
                onSaveMissedDiff={saveMissedDiff}
                onStartAddingMissedDiff={() => setIsAddingMissedDiff(true)}
              />
            </>
          ) : (
            <div className="quality-state">请选择左侧质量样本</div>
          )}
        </main>
      </div>
    </section>
  );
}

function TaskReviewPanel({
  taskReviewId,
  taskReview,
  taskReviewError,
  isLoadingTaskReview,
  onChangeTaskReviewId,
  onLoadTaskReview,
}: {
  taskReviewId: string;
  taskReview: QualityTaskReviewResponse | null;
  taskReviewError: string;
  isLoadingTaskReview: boolean;
  onChangeTaskReviewId: (value: string) => void;
  onLoadTaskReview: () => void;
}) {
  return (
    <section className="quality-section quality-task-review" aria-labelledby="quality-task-review-title">
      <div className="quality-section-title">
        <h2 id="quality-task-review-title">任务复盘</h2>
      </div>
      <form
        className="quality-task-review-form"
        onSubmit={(event) => {
          event.preventDefault();
          onLoadTaskReview();
        }}
      >
        <label htmlFor="quality-task-review-id">任务 ID</label>
        <input
          id="quality-task-review-id"
          type="text"
          value={taskReviewId}
          onChange={(event) => onChangeTaskReviewId(event.target.value)}
        />
        <button type="submit" disabled={isLoadingTaskReview || taskReviewId.trim().length === 0}>
          {isLoadingTaskReview ? "加载中..." : "加载任务复盘"}
        </button>
      </form>
      {taskReviewError && (
        <div className="quality-inline-error" role="alert">
          {taskReviewError}
        </div>
      )}
      {taskReview && (
        <div className="quality-task-review-body">
          <div className="quality-task-review-metrics" aria-label="任务复盘统计">
            <span>历史 {taskReview.historical_diff_count}</span>
            <span>保留 {taskReview.retained_diff_count}</span>
            <span>抑制 {taskReview.suppressed_diff_count}</span>
            <span>状态 {taskReview.status || "-"}</span>
            <span>OCR {formatOcrSummary(taskReview.ocr_quality_summary)}</span>
          </div>
          <TaskSuppressedDiffList diffs={taskReview.suppressed_diffs} />
          <TaskRetainedDiffList diffs={taskReview.retained_diffs} />
        </div>
      )}
    </section>
  );
}

function TaskSuppressedDiffList({ diffs }: { diffs: QualityTaskSuppressedDiff[] }) {
  return (
    <section className="quality-task-review-list" aria-labelledby="quality-suppressed-diffs-title">
      <h3 id="quality-suppressed-diffs-title">被抑制 diff</h3>
      {diffs.length === 0 ? (
        <div className="quality-state compact">当前质量过滤未抑制历史 diff</div>
      ) : (
        diffs.map((diff) => (
          <article key={diff.diff_id}>
            <div>
              <strong>{diff.diff_id}</strong>
              <span>{diff.suppression_reason || "-"}</span>
            </div>
            <p>{diff.title || "-"}</p>
            <small>
              {diff.diff_type} / {diff.source_type || "-"}
              {diff.quality_decisions.length > 0 ? ` / ${diff.quality_decisions.join(" / ")}` : ""}
            </small>
            <code>
              {diff.original_snippet || "-"} {"->"} {diff.compare_snippet || "-"}
            </code>
          </article>
        ))
      )}
    </section>
  );
}

function TaskRetainedDiffList({ diffs }: { diffs: QualityTaskReviewDiff[] }) {
  return (
    <section className="quality-task-review-list" aria-labelledby="quality-retained-diffs-title">
      <h3 id="quality-retained-diffs-title">保留 diff</h3>
      {diffs.length === 0 ? (
        <div className="quality-state compact">当前质量过滤未保留历史 diff</div>
      ) : (
        diffs.map((diff) => (
          <article key={diff.diff_id}>
            <div>
              <strong>{diff.diff_id}</strong>
              <span>{diff.quality_status || "-"}</span>
            </div>
            <p>{diff.title || "-"}</p>
            <small>
              {diff.diff_type} / {diff.source_type || "-"} / match {diff.match_score ?? "-"}
              {diff.review_flags.length > 0 ? ` / ${diff.review_flags.join(" / ")}` : ""}
            </small>
            <code>
              {diff.original_snippet || "-"} {"->"} {diff.compare_snippet || "-"}
            </code>
          </article>
        ))
      )}
    </section>
  );
}

function formatOcrSummary(summary: Record<string, unknown> | null | undefined): string {
  if (!summary) return "-";
  const status = summary.status;
  return typeof status === "string" && status ? status : "-";
}

function QualityRunBar({
  isRunningQuality,
  runResult,
  onRunEvaluation,
  onRunRegression,
}: {
  isRunningQuality: boolean;
  runResult: QualityRunResponse | null;
  onRunEvaluation: () => void;
  onRunRegression: () => void;
}) {
  return (
    <section className="quality-section quality-runbar" aria-labelledby="quality-run-title">
      <div className="quality-section-title">
        <h2 id="quality-run-title">RunResults</h2>
        <div>
          <button type="button" onClick={onRunEvaluation} disabled={isRunningQuality}>
            {isRunningQuality ? "运行中..." : "运行评估"}
          </button>
          <button type="button" onClick={onRunRegression} disabled={isRunningQuality}>
            运行回归
          </button>
        </div>
      </div>
      {runResult && <QualityRunResult result={runResult} />}
    </section>
  );
}

function QualityRunResult({ result }: { result: QualityRunResponse }) {
  const aggregate = readRecord(result.report.aggregate);
  const precision = aggregate?.precision;
  const recall = aggregate?.recall;
  const knownFalsePositiveCount = aggregate?.known_false_positive_regression_count;
  const thresholdFailures = result.report.threshold_failures;
  const comparison = readRecord(result.comparison);
  const failedGates = comparison?.failed_gates;

  return (
    <div className="quality-run-result">
      <span>{result.status}</span>
      <span>Run {result.run_id}</span>
      {precision !== undefined && <strong>Precision {String(precision)}</strong>}
      {recall !== undefined && <strong>Recall {String(recall)}</strong>}
      {knownFalsePositiveCount !== undefined && <span>Known FP {String(knownFalsePositiveCount)}</span>}
      {thresholdFailures !== undefined && <span>threshold_failures {formatRunValue(thresholdFailures)}</span>}
      {failedGates !== undefined && <span>failed_gates {formatRunValue(failedGates)}</span>}
    </div>
  );
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function formatRunValue(value: unknown): string {
  if (Array.isArray(value)) return value.length === 0 ? "[]" : value.join(" / ");
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function CaseSummary({ summary }: { summary: QualityCaseSummary }) {
  return (
    <section className="quality-section" aria-labelledby="quality-summary-title">
      <h2 id="quality-summary-title">CaseSummary</h2>
      <dl className="quality-meta-grid">
        <MetaItem label="Case ID" value={summary.case_id} />
        <MetaItem label="Dataset split" value={summary.dataset_split} />
        <MetaItem label="Source task" value={summary.source_task_id} />
        <MetaItem label="Schema" value={summary.schema_version} />
        <MetaItem label="Original" value={summary.original_filename} />
        <MetaItem label="Compare" value={summary.compare_filename} />
        <MetaItem label="Expected" value={`${summary.approved_expected_count} approved`} />
        <MetaItem label="Actual" value={`${summary.actual_diff_count} diffs`} />
        <MetaItem label="Tags" value={summary.case_tags.length > 0 ? summary.case_tags.join(" / ") : "-"} />
        <MetaItem label="Baseline" value={summary.baseline_required ? "required" : "optional"} />
      </dl>
    </section>
  );
}

function ActualDiffList({ diffs }: { diffs: QualityActualDiffSummary[] }) {
  return (
    <section className="quality-section" aria-labelledby="quality-actual-title">
      <h2 id="quality-actual-title">ActualDiffList</h2>
      {diffs.length === 0 ? (
        <div className="quality-state">暂无实际差异</div>
      ) : (
        diffs.map((diff) => (
          <article className="quality-diff-row" key={diff.diff_id}>
            <div>
              <strong>{diff.title || diff.diff_id}</strong>
              <span>{diff.diff_id}</span>
            </div>
            <dl>
              <dt>Type</dt>
              <dd>{diff.diff_type}</dd>
              <dt>Source</dt>
              <dd>{diff.source_type}</dd>
              <dt>Status</dt>
              <dd>{diff.quality_status}</dd>
            </dl>
            {diff.review_flags.length > 0 && <small>{diff.review_flags.join(" / ")}</small>}
          </article>
        ))
      )}
    </section>
  );
}

function ExpectedDiffList({
  diffs,
  editingEvidenceIndex,
  evidenceError,
  evidenceJson,
  isAddingMissedDiff,
  isSavingEvidence,
  isSavingMissedDiff,
  missedTitle,
  onChangeMissedTitle,
  onChangeEvidenceJson,
  onOpenEvidenceEditor,
  onMarkExpectedDiff,
  onMarkFalsePositive,
  onSaveEvidence,
  onSaveMissedDiff,
  onStartAddingMissedDiff,
}: {
  diffs: ExpectedDiff[];
  editingEvidenceIndex: number | null;
  evidenceError: string;
  evidenceJson: string;
  isAddingMissedDiff: boolean;
  isSavingEvidence: boolean;
  isSavingMissedDiff: boolean;
  missedTitle: string;
  onChangeMissedTitle: (value: string) => void;
  onChangeEvidenceJson: (value: string) => void;
  onOpenEvidenceEditor: (index: number, diff: ExpectedDiff) => void;
  onMarkExpectedDiff: (index: number, payload: Partial<ExpectedDiff>) => void;
  onMarkFalsePositive: (index: number) => void;
  onSaveEvidence: () => void;
  onSaveMissedDiff: () => void;
  onStartAddingMissedDiff: () => void;
}) {
  return (
    <section className="quality-section" aria-labelledby="quality-expected-title">
      <div className="quality-section-title">
        <h2 id="quality-expected-title">ExpectedDiffList</h2>
        <button type="button" onClick={onStartAddingMissedDiff}>
          补录漏报
        </button>
      </div>
      {isAddingMissedDiff && (
        <div className="quality-missed-form">
          <label htmlFor="quality-missed-title">漏报标题</label>
          <input
            id="quality-missed-title"
            type="text"
            value={missedTitle}
            onChange={(event) => onChangeMissedTitle(event.target.value)}
          />
          <button type="button" onClick={onSaveMissedDiff} disabled={isSavingMissedDiff}>
            {isSavingMissedDiff ? "保存中..." : "保存漏报"}
          </button>
        </div>
      )}
      {diffs.length === 0 ? (
        <div className="quality-state">暂无期望差异</div>
      ) : (
        diffs.map((diff, index) => (
          <article className="quality-diff-row" key={`${diff.title_contains ?? diff.source_actual_diff_id ?? "expected"}-${index}`}>
            <div>
              <strong>{diff.title_contains || diff.source_actual_diff_id || `Expected diff ${index + 1}`}</strong>
              <span>{diff.review_status ?? "DRAFT"}</span>
            </div>
            <dl>
              <dt>Type</dt>
              <dd>{diff.diff_type ?? "-"}</dd>
              <dt>Source</dt>
              <dd>{diff.source_type ?? "-"}</dd>
              <dt>Severity</dt>
              <dd>{diff.severity ?? "-"}</dd>
            </dl>
            {(diff.original_contains || diff.compare_contains) && (
              <small>
                {diff.original_contains ?? "-"} {"->"} {diff.compare_contains ?? "-"}
              </small>
            )}
            <div className="quality-diff-actions">
              <button type="button" onClick={() => onMarkExpectedDiff(index, { review_status: "APPROVED" })}>
                标为真实差异
              </button>
              <button type="button" onClick={() => onMarkExpectedDiff(index, { review_status: "DRAFT" })}>
                待复核
              </button>
              <button type="button" onClick={() => onMarkFalsePositive(index)}>
                标为误报
              </button>
              <button type="button" onClick={() => onOpenEvidenceEditor(index, diff)}>
                编辑证据
              </button>
            </div>
            {editingEvidenceIndex === index && (
              <div className="quality-evidence-editor">
                <label htmlFor={`quality-evidence-json-${index}`}>证据 JSON</label>
                <textarea
                  id={`quality-evidence-json-${index}`}
                  value={evidenceJson}
                  onChange={(event) => onChangeEvidenceJson(event.target.value)}
                />
                {evidenceError && (
                  <div className="quality-inline-error" role="alert">
                    {evidenceError}
                  </div>
                )}
                <div>
                  <button type="button" onClick={onSaveEvidence} disabled={isSavingEvidence}>
                    {isSavingEvidence ? "保存中..." : "保存证据"}
                  </button>
                </div>
              </div>
            )}
          </article>
        ))
      )}
    </section>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
