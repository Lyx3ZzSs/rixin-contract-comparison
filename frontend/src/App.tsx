import { useMemo } from "react";

import { type CurrentUser } from "./auth/currentUser";
import { ComparisonRecordsPage } from "./pages/ComparisonRecordsPage";
import { ResultPage } from "./pages/ResultPage";
import { UploadPage } from "./pages/UploadPage";
import { navigateHome, navigateToComparisonRecords, navigateToTask } from "./lib/routes";
import { useApp } from "./lib/state";

interface AppProps {
  currentUser: CurrentUser;
  accessToken?: string;
  onSignOut?: () => void;
}

export function App({ currentUser, accessToken = "", onSignOut }: AppProps) {
  const { state, dispatch } = useApp();
  const { route, isSidebarExpanded, isComparisonMenuOpen } = state;

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
    if (route.name === "task") return <ResultPage taskId={route.taskId} onBack={navigateHome} accessToken={accessToken} />;
    if (route.name === "records") return <ComparisonRecordsPage onOpenTask={navigateToTask} onCreateComparison={navigateHome} />;
    return <UploadPage onTaskCreated={handleTaskCreated} onOpenRecords={navigateToComparisonRecords} />;
  }, [accessToken, route]);

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
              <span className="oa-nav-icon" aria-hidden="true">[]</span>
              <span>{isSidebarExpanded ? "合同智能对比" : "对比"}</span>
              {isSidebarExpanded && <span className="oa-menu-chevron" aria-hidden="true" />}
            </button>
            {isSidebarExpanded && isComparisonMenuOpen && (
              <div className="oa-subnav" aria-label="合同智能对比菜单">
                <button className={route.name === "home" ? "active" : ""} type="button" onClick={navigateHome}>
                  <span className="oa-subnav-dot" aria-hidden="true" />
                  <span>合同对比</span>
                </button>
                <button className={route.name === "records" ? "active" : ""} type="button" onClick={navigateToComparisonRecords} aria-current={route.name === "records" ? "page" : undefined}>
                  <span className="oa-history-icon" aria-hidden="true" />
                  <span>对比记录</span>
                </button>
              </div>
            )}
          </div>
        </nav>
        {isSidebarExpanded && (
          <div className="oa-user-panel" aria-label="当前用户">
            <span className="oa-user-avatar" aria-hidden="true">{currentUser.displayName.slice(0, 1).toUpperCase()}</span>
            <div>
              <small>{currentUser.departmentName || "当前用户"}</small>
              <strong>{currentUser.displayName}</strong>
            </div>
            {onSignOut && <button type="button" onClick={onSignOut}>退出登录</button>}
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
      <div className="oa-main"><main className="app-shell">{content}</main></div>
    </div>
  );
}
