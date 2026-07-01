
export type AppRoute =
  | { name: "home" }
  | { name: "records" }
  | { name: "quality" }
  | { name: "task"; taskId: string };

export function readRoute(pathname = window.location.pathname): AppRoute {
  const match = pathname.match(/^\/tasks\/([^/]+)$/);
  if (match) {
    return { name: "task", taskId: decodeURIComponent(match[1]) };
  }
  if (pathname === "/compare/records") {
    return { name: "records" };
  }
  if (pathname === "/quality/workbench") {
    return { name: "quality" };
  }
  return { name: "home" };
}

export function navigateTo(path: string): void {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function navigateHome(): void {
  navigateTo("/");
}

export function navigateToTask(taskId: string): void {
  navigateTo(`/tasks/${encodeURIComponent(taskId)}`);
}

export function navigateToComparisonRecords(): void {
  navigateTo("/compare/records");
}

export function navigateToQualityWorkbench(): void {
  navigateTo("/quality/workbench");
}
