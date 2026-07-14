import { UserManager, WebStorageStateStore } from "oidc-client-ts";

import { getOidcConfig } from "./config";

let userManager: UserManager | undefined;

export function getUserManager(): UserManager {
  if (!userManager) {
    const userStore = new WebStorageStateStore({ store: window.sessionStorage });
    const stateStore = new WebStorageStateStore({ store: window.sessionStorage });
    userManager = new UserManager({
      ...getOidcConfig(),
      userStore,
      stateStore,
      loadUserInfo: false,
    });
  }
  return userManager;
}
