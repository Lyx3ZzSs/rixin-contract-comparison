import { useEffect, useRef, type ReactNode } from "react";
import { hasAuthParams, useAuth } from "react-oidc-context";

import { currentInternalPath, REAUTH_ATTEMPT_KEY } from "./config";

export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const redirectStarted = useRef(false);
  const processingCallback = hasAuthParams();

  useEffect(() => {
    if (
      processingCallback ||
      auth.isLoading ||
      auth.activeNavigator ||
      auth.isAuthenticated ||
      auth.error ||
      redirectStarted.current
    ) {
      return;
    }
    redirectStarted.current = true;
    void auth.signinRedirect({ state: { returnTo: currentInternalPath() } });
  }, [auth, processingCallback]);

  const retry = () => {
    redirectStarted.current = false;
    window.sessionStorage.removeItem(REAUTH_ATTEMPT_KEY);
    void auth.signinRedirect({ state: { returnTo: currentInternalPath() } });
  };

  if (auth.error) {
    return (
      <section className="auth-status" role="alert">
        <p>统一身份认证失败。</p>
        <button type="button" onClick={retry}>重新登录</button>
      </section>
    );
  }
  if (processingCallback) return <section className="auth-status">正在完成统一身份认证…</section>;
  if (auth.isLoading || auth.activeNavigator) return <section className="auth-status">正在初始化统一身份认证…</section>;
  if (auth.isAuthenticated) return <>{children}</>;
  return <section className="auth-status">正在跳转至统一身份认证…</section>;
}
