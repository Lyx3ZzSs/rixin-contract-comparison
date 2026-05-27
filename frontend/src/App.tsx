import { useEffect, useMemo, useState } from "react";

import { LoginPage } from "./pages/LoginPage";
import { ExtractionFieldsPage } from "./pages/ExtractionFieldsPage";
import { ExtractionPage } from "./pages/ExtractionPage";
import { ExtractionRecordsPage } from "./pages/ExtractionRecordsPage";
import { ComparisonRecordsPage } from "./pages/ComparisonRecordsPage";
import { ResultPage } from "./pages/ResultPage";
import { UploadPage } from "./pages/UploadPage";

const AUTH_STORAGE_KEY = "rixin_contract_auth_user";

function readRoute():
  | { name: "home" }
  | { name: "records" }
  | { name: "extract" }
  | { name: "extractRecords" }
  | { name: "extractFields" }
  | { name: "task"; taskId: string } {
  const match = window.location.pathname.match(/^\/tasks\/([^/]+)$/);
  if (match) {
    return { name: "task", taskId: decodeURIComponent(match[1]) };
  }
  if (window.location.pathname === "/compare/records") {
    return { name: "records" };
  }
  if (window.location.pathname === "/extract") {
    return { name: "extract" };
  }
  if (window.location.pathname === "/extract/records") {
    return { name: "extractRecords" };
  }
  if (window.location.pathname === "/extract/fields") {
    return { name: "extractFields" };
  }
  return { name: "home" };
}

function navigateToTask(taskId: string): void {
  window.history.pushState({}, "", `/tasks/${encodeURIComponent(taskId)}`);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function navigateToExtraction(): void {
  window.history.pushState({}, "", "/extract");
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function navigateToExtractionRecords(): void {
  window.history.pushState({}, "", "/extract/records");
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function navigateToExtractionFields(): void {
  window.history.pushState({}, "", "/extract/fields");
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function navigateToComparisonRecords(): void {
  window.history.pushState({}, "", "/compare/records");
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function App() {
  const [route, setRoute] = useState(readRoute);
  const [currentUser, setCurrentUser] = useState(() => window.localStorage.getItem(AUTH_STORAGE_KEY));
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false);
  const [isComparisonMenuOpen, setIsComparisonMenuOpen] = useState(true);
  const [isExtractionMenuOpen, setIsExtractionMenuOpen] = useState(true);

  useEffect(() => {
    const onPopState = () => setRoute(readRoute());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function handleLogin(username: string, password: string): boolean {
    if (username === "admin" && password === "123456") {
      window.localStorage.setItem(AUTH_STORAGE_KEY, username);
      setCurrentUser(username);
      return true;
    }
    return false;
  }

  function handleLogout() {
    window.localStorage.removeItem(AUTH_STORAGE_KEY);
    setCurrentUser(null);
    setIsSidebarExpanded(false);
    navigateHome();
  }

  function handleTaskCreated(taskId: string) {
    void taskId;
  }

  function handleComparisonMenuClick() {
    if (!isSidebarExpanded) {
      navigateHome();
      return;
    }
    setIsComparisonMenuOpen((value) => !value);
  }

  function handleExtractionMenuClick() {
    if (!isSidebarExpanded) {
      setIsSidebarExpanded(true);
      setIsExtractionMenuOpen(true);
      return;
    }
    setIsExtractionMenuOpen((value) => !value);
  }

  const content = useMemo(() => {
    if (route.name === "task") {
      return <ResultPage taskId={route.taskId} onBack={() => navigateHome()} />;
    }
    if (route.name === "extract") {
      return <ExtractionPage />;
    }
    if (route.name === "extractRecords") {
      return <ExtractionRecordsPage onCreateExtraction={navigateToExtraction} />;
    }
    if (route.name === "extractFields") {
      return <ExtractionFieldsPage />;
    }
    if (route.name === "records") {
      return <ComparisonRecordsPage onOpenTask={navigateToTask} onCreateComparison={navigateHome} />;
    }
    return <UploadPage onTaskCreated={handleTaskCreated} onOpenRecords={navigateToComparisonRecords} />;
  }, [route]);

  if (!currentUser) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return (
    <div className={isSidebarExpanded ? "oa-frame sidebar-expanded" : "oa-frame"}>
      <aside className="oa-sidebar" aria-label="主导航">
        <strong className="oa-sidebar-title">{isSidebarExpanded ? "合同智能助手" : "合同"}</strong>
        <nav className="oa-nav">
          <div className={isComparisonMenuOpen ? "oa-nav-group open" : "oa-nav-group"}>
            <button
              className={route.name === "task" || route.name === "home" || route.name === "records" ? "active" : ""}
              type="button"
              onClick={handleComparisonMenuClick}
              aria-expanded={isSidebarExpanded ? isComparisonMenuOpen : undefined}
            >
              <span className="oa-nav-icon" aria-hidden="true">
                []
              </span>
              <span>{isSidebarExpanded ? "合同智能对比" : "对比"}</span>
              {isSidebarExpanded && <span className="oa-menu-chevron" aria-hidden="true" />}
            </button>
            {isSidebarExpanded && isComparisonMenuOpen && (
              <div className="oa-subnav" aria-label="合同智能对比菜单">
                <button className={route.name === "home" ? "active" : ""} type="button" onClick={navigateHome}>
                  <span className="oa-subnav-dot" aria-hidden="true" />
                  <span>合同对比</span>
                </button>
                <button
                  className={route.name === "records" ? "active" : ""}
                  type="button"
                  onClick={navigateToComparisonRecords}
                  aria-current={route.name === "records" ? "page" : undefined}
                >
                  <span className="oa-history-icon" aria-hidden="true" />
                  <span>对比记录</span>
                </button>
              </div>
            )}
          </div>
          <div className={isExtractionMenuOpen ? "oa-nav-group open" : "oa-nav-group"}>
            <button
              className={
                route.name === "extract" || route.name === "extractRecords" || route.name === "extractFields"
                  ? "active"
                  : ""
              }
              type="button"
              onClick={handleExtractionMenuClick}
              aria-expanded={isSidebarExpanded ? isExtractionMenuOpen : undefined}
            >
              <span className="oa-extract-icon" aria-hidden="true" />
              <span>{isSidebarExpanded ? "合同智能提取" : "提取"}</span>
              {isSidebarExpanded && <span className="oa-menu-chevron" aria-hidden="true" />}
            </button>
            {isSidebarExpanded && isExtractionMenuOpen && (
              <div className="oa-subnav" aria-label="合同智能提取菜单">
                <button className={route.name === "extract" ? "active" : ""} type="button" onClick={navigateToExtraction}>
                  <span className="oa-subnav-dot" aria-hidden="true" />
                  <span>合同提取</span>
                </button>
                <button
                  className={route.name === "extractRecords" ? "active" : ""}
                  type="button"
                  onClick={navigateToExtractionRecords}
                  aria-current={route.name === "extractRecords" ? "page" : undefined}
                >
                  <span className="oa-history-icon" aria-hidden="true" />
                  <span>提取记录</span>
                </button>
                <button
                  className={route.name === "extractFields" ? "active" : ""}
                  type="button"
                  onClick={navigateToExtractionFields}
                  aria-current={route.name === "extractFields" ? "page" : undefined}
                >
                  <span className="oa-field-icon" aria-hidden="true" />
                  <span>提取字段管理</span>
                </button>
              </div>
            )}
          </div>
        </nav>
        {isSidebarExpanded && (
          <div className="oa-user-panel" aria-label="当前用户">
            <span className="oa-user-avatar" aria-hidden="true">
              {currentUser.slice(0, 1).toUpperCase()}
            </span>
            <div>
              <small>当前用户</small>
              <strong>{currentUser}</strong>
            </div>
            <button type="button" onClick={handleLogout}>
              退出登录
            </button>
          </div>
        )}
        <button
          className="oa-sidebar-footer"
          type="button"
          onClick={() => setIsSidebarExpanded((value) => !value)}
          aria-label={isSidebarExpanded ? "收起侧边栏" : "展开侧边栏"}
          aria-pressed={isSidebarExpanded}
        >
          <span aria-hidden="true" />
        </button>
      </aside>

      <div className="oa-main">
        <main className="app-shell">{content}</main>
      </div>
    </div>
  );
}

function navigateHome(): void {
  window.history.pushState({}, "", "/");
  window.dispatchEvent(new PopStateEvent("popstate"));
}
