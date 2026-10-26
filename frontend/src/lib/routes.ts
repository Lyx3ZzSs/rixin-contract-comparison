
import { stripAppBasePath, withAppBasePath } from "./env";

export type AppRoute =
  | { name: "home" }
  | { name: "records" }
  | { name: "task"; taskId: string };

export function readRoute(pathname = window.location.pathname): AppRoute {
  const routePath = stripAppBasePath(pathname);
  const match = routePath.match(/^\/tasks\/([^/]+)$/);
  if (match) {
    return { name: "task", taskId: decodeURIComponent(match[1]) };
  }
  if (routePath === "/compare/records") {
    return { name: "records" };
  }
  return { name: "home" };
}

export function navigateTo(path: string): void {
  window.history.pushState({}, "", withAppBasePath(path));
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
