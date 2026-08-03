import { StrictMode, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { AuthProvider, useAuth } from "react-oidc-context";
import type { User } from "oidc-client-ts";

import { App } from "./App";
import { AuthGate } from "./auth/AuthGate";
import { getAuthMode, getDisabledCurrentUser, REAUTH_ATTEMPT_KEY, safeReturnTo } from "./auth/config";
import { buildCurrentUser, type CurrentUser } from "./auth/currentUser";
import { getUserManager } from "./auth/userManager";
import { AppProvider } from "./lib/state";
import "./styles.css";

export function handleSigninCallback(user?: User): void {
  window.sessionStorage.removeItem(REAUTH_ATTEMPT_KEY);
  window.history.replaceState({}, document.title, safeReturnTo(user?.state));
}

function OidcRoot() {
  const [retry, setRetry] = useState(0);
  const result = useMemo(() => {
    try {
      return { manager: getUserManager(), error: "" };
    } catch (error) {
      return { manager: null, error: error instanceof Error ? error.message : "OIDC 配置无效。" };
    }
  }, [retry]);

  if (!result.manager) {
    return (
      <section className="auth-status" role="alert">
        <p>{result.error}</p>
        <button type="button" onClick={() => setRetry((value) => value + 1)}>重新检查配置</button>
      </section>
    );
  }

  return (
    <AuthProvider userManager={result.manager} onSigninCallback={handleSigninCallback}>
      <AuthGate>
        <OidcApplication />
      </AuthGate>
    </AuthProvider>
  );
}

function OidcApplication() {
  const auth = useAuth();
  if (!auth.user?.access_token) return null;
  const currentUser = buildCurrentUser(auth.user.access_token, auth.user.profile);
  return (
    <AppProvider>
      <App
        currentUser={currentUser}
        accessToken={auth.user.access_token}
        onSignOut={() => void auth.signoutRedirect()}
      />
    </AppProvider>
  );
}

function DisabledRoot({ currentUser }: { currentUser: CurrentUser }) {
  return (
    <AppProvider>
      <App currentUser={currentUser} />
    </AppProvider>
  );
}

function Root() {
  try {
    return getAuthMode() === "disabled"
      ? <DisabledRoot currentUser={getDisabledCurrentUser()} />
      : <OidcRoot />;
  } catch (error) {
    return <section className="auth-status" role="alert"><p>{error instanceof Error ? error.message : "认证配置无效。"}</p></section>;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
