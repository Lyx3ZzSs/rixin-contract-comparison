import { createContext, useContext, useReducer, useEffect, type ReactNode } from "react";

import { readRoute, navigateHome, type AppRoute } from "./routes";

const AUTH_STORAGE_KEY = "rixin_contract_auth_user";

export interface AppState {
  currentUser: string | null;
  route: AppRoute;
  isSidebarExpanded: boolean;
  isComparisonMenuOpen: boolean;
  isExtractionMenuOpen: boolean;
}

export type Action =
  | { type: "LOGIN"; username: string }
  | { type: "LOGOUT" }
  | { type: "SET_ROUTE"; route: AppRoute }
  | { type: "TOGGLE_SIDEBAR" }
  | { type: "TOGGLE_COMPARISON_MENU" }
  | { type: "TOGGLE_EXTRACTION_MENU" };

function getInitialState(): AppState {
  return {
    currentUser: window.localStorage.getItem(AUTH_STORAGE_KEY),
    route: readRoute(),
    isSidebarExpanded: false,
    isComparisonMenuOpen: true,
    isExtractionMenuOpen: true,
  };
}

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "LOGIN":
      window.localStorage.setItem(AUTH_STORAGE_KEY, action.username);
      return { ...state, currentUser: action.username };
    case "LOGOUT":
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
      return { ...state, currentUser: null, isSidebarExpanded: false };
    case "SET_ROUTE":
      return { ...state, route: action.route };
    case "TOGGLE_SIDEBAR":
      return { ...state, isSidebarExpanded: !state.isSidebarExpanded };
    case "TOGGLE_COMPARISON_MENU":
      return { ...state, isComparisonMenuOpen: !state.isComparisonMenuOpen };
    case "TOGGLE_EXTRACTION_MENU":
      return { ...state, isExtractionMenuOpen: !state.isExtractionMenuOpen };
    default:
      return state;
  }
}

interface AppContextValue {
  state: AppState;
  dispatch: React.Dispatch<Action>;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, getInitialState);

  useEffect(() => {
    const onPopState = () => dispatch({ type: "SET_ROUTE", route: readRoute() });
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  return <AppContext.Provider value={{ state, dispatch }}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used within AppProvider");
  return ctx;
}
