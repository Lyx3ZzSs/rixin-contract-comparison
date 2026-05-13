import { useEffect, useMemo, useState } from "react";

import { LoginPage } from "./pages/LoginPage";
import { ResultPage } from "./pages/ResultPage";
import { UploadPage } from "./pages/UploadPage";

const AUTH_STORAGE_KEY = "rixin_contract_auth_user";

function readRoute(): { name: "home" } | { name: "task"; taskId: string } {
  const match = window.location.pathname.match(/^\/tasks\/([^/]+)$/);
  if (match) {
    return { name: "task", taskId: decodeURIComponent(match[1]) };
  }
  return { name: "home" };
}

function navigateToTask(taskId: string): void {
  window.history.pushState({}, "", `/tasks/${encodeURIComponent(taskId)}`);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function App() {
  const [route, setRoute] = useState(readRoute);
  const [currentUser, setCurrentUser] = useState(() => window.localStorage.getItem(AUTH_STORAGE_KEY));
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false);

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

  const content = useMemo(() => {
    if (route.name === "task") {
      return <ResultPage taskId={route.taskId} onBack={() => navigateHome()} />;
    }
    return <UploadPage onTaskCreated={navigateToTask} />;
  }, [route]);

  if (!currentUser) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return (
    <div className={isSidebarExpanded ? "oa-frame sidebar-expanded" : "oa-frame"}>
      <aside className="oa-sidebar" aria-label="主导航">
        <strong className="oa-sidebar-title">{isSidebarExpanded ? "合同智能对比" : "对比"}</strong>
        <nav className="oa-nav">
          <button className="active" type="button" onClick={navigateHome}>
            <span className="oa-nav-icon" aria-hidden="true">
              []
            </span>
            <span>{isSidebarExpanded ? "对比合同" : "合同对比"}</span>
          </button>
          <button type="button" disabled>
            <span className="oa-history-icon" aria-hidden="true" />
            <span>对比记录</span>
          </button>
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
