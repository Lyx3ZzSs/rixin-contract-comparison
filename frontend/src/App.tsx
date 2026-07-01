import { useMemo } from "react";

import { LoginPage } from "./pages/LoginPage";
import { ComparisonRecordsPage } from "./pages/ComparisonRecordsPage";
import { QualityWorkbenchPage } from "./pages/QualityWorkbenchPage";
import { ResultPage } from "./pages/ResultPage";
import { UploadPage } from "./pages/UploadPage";
import {
  navigateHome,
  navigateToComparisonRecords,
  navigateToQualityWorkbench,
  navigateToTask,
} from "./lib/routes";
import { useApp } from "./lib/state";

export function App() {
  const { state, dispatch } = useApp();
  const { currentUser, route, isSidebarExpanded, isComparisonMenuOpen } = state;

  function handleLogin(username: string, password: string): boolean {
    if (username.trim() !== "" && password.trim() !== "") {
      dispatch({ type: "LOGIN", username });
      return true;
    }
    return false;
  }

  function handleLogout() {
    dispatch({ type: "LOGOUT" });
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
    dispatch({ type: "TOGGLE_COMPARISON_MENU" });
  }

  const content = useMemo(() => {
    if (route.name === "task") {
      return <ResultPage taskId={route.taskId} onBack={() => navigateHome()} />;
    }
    if (route.name === "records") {
      return <ComparisonRecordsPage onOpenTask={navigateToTask} onCreateComparison={navigateHome} />;
    }
    if (route.name === "quality") {
      return <QualityWorkbenchPage />;
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
              className={
                route.name === "task" || route.name === "home" || route.name === "records" || route.name === "quality"
                  ? "active"
                  : ""
              }
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
                <button
                  className={route.name === "quality" ? "active" : ""}
                  type="button"
                  onClick={navigateToQualityWorkbench}
                  aria-current={route.name === "quality" ? "page" : undefined}
                >
                  <span className="oa-history-icon" aria-hidden="true" />
                  <span>质量工作台</span>
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
          onClick={() => dispatch({ type: "TOGGLE_SIDEBAR" })}
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
