import { createContext, useContext, useEffect, useReducer, type ReactNode } from "react";

import { readRoute, type AppRoute } from "./routes";

export interface AppState {
  route: AppRoute;
  isSidebarExpanded: boolean;
  isComparisonMenuOpen: boolean;
}

export type Action =
  | { type: "SET_ROUTE"; route: AppRoute }
  | { type: "TOGGLE_SIDEBAR" }
  | { type: "TOGGLE_COMPARISON_MENU" };

function getInitialState(): AppState {
  return {
    route: readRoute(),
    isSidebarExpanded: false,
    isComparisonMenuOpen: true,
  };
}

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "SET_ROUTE":
      return { ...state, route: action.route };
    case "TOGGLE_SIDEBAR":
      return { ...state, isSidebarExpanded: !state.isSidebarExpanded };
    case "TOGGLE_COMPARISON_MENU":
      return { ...state, isComparisonMenuOpen: !state.isComparisonMenuOpen };
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
