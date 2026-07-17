import type { TaskStatus, TaskTerminalReason } from "../types";

export function taskStatusLabel(status: TaskStatus, terminalReason: TaskTerminalReason): string {
  if (status === "FAILED" && terminalReason === "CANCELLED") return "已取消";
  if (status === "FAILED" && terminalReason === "SUBMISSION_FAILED") return "提交失败";
  if (status === "FAILED") return "执行失败";
  if (status === "COMPLETED") return "已完成";
  return "处理中";
}

export function canRetryTask(
  status: TaskStatus,
  terminalReason: TaskTerminalReason,
  retryEligible?: boolean,
): boolean {
  if (status !== "FAILED") return false;
  if (terminalReason === "EXECUTION_FAILED") return retryEligible !== false;
  return terminalReason === "SUBMISSION_FAILED" && retryEligible === true;
}
