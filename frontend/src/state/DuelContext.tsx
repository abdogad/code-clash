/* Context wiring for the screen state machine. useApp() -> AppState, useDispatch()
   -> dispatch(Action), split so dispatch-only components don't re-render on every
   state change. */

import {
  createContext,
  useContext,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";
import { type Action, type AppState, initialState, reducer } from "./duelMachine";
import { auth } from "../hooks/api";

const StateCtx = createContext<AppState | null>(null);
const DispatchCtx = createContext<Dispatch<Action> | null>(null);

export function DuelProvider({ children }: { children: ReactNode }) {
  // A returning user (the `authed` hint is set) starts on the "boot" loader, not
  // sign-in — useBoot resolves it to lobby/duel (or back to auth if stale). Decided
  // synchronously so the first render is correct and sign-in never flashes.
  const [state, dispatch] = useReducer(reducer, initialState, (s): AppState => ({
    ...s,
    screen: auth.isAuthed ? "boot" : "auth",
  }));
  return (
    <StateCtx.Provider value={state}>
      <DispatchCtx.Provider value={dispatch}>{children}</DispatchCtx.Provider>
    </StateCtx.Provider>
  );
}

export function useApp(): AppState {
  const ctx = useContext(StateCtx);
  if (!ctx) throw new Error("useApp must be used within <DuelProvider>");
  return ctx;
}

export function useDispatch(): Dispatch<Action> {
  const ctx = useContext(DispatchCtx);
  if (!ctx) throw new Error("useDispatch must be used within <DuelProvider>");
  return ctx;
}
