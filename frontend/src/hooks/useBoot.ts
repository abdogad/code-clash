/* On load, resume a session from the auth cookies — and resume mid-flow: a
   returning user who's in an active duel or sitting in the queue lands back where
   they were (survives reloads AND a backend restart, via GET /api/queue/status).
   /auth/me silently refreshes an expired access token (see api()) before giving up. */
import { useEffect, useRef } from "react";
import type { Dispatch } from "react";
import type { Action } from "../state/duelMachine";
import type { Me } from "../state/types";
import { api, auth, type QueueStatus } from "./api";
import { enterMatch } from "./enterMatch";

export function useBoot(dispatch: Dispatch<Action>) {
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return; // guard StrictMode's double-invoke
    booted.current = true;
    if (!auth.isAuthed) return; // no session: initial screen is already "auth"
    (async () => {
      const meRes = await api<Me>("/auth/me");
      if (!meRes.ok || !meRes.data) {
        auth.clear(); // expired / invalid -> leave "boot" for the sign-in screen
        dispatch({ type: "SET_SCREEN", screen: "auth" });
        return;
      }
      const me = meRes.data;
      dispatch({ type: "SET_ME", me });

      const st = await api<QueueStatus>("/queue/status");
      if (st.ok && st.data?.status === "matched") {
        if (await enterMatch(dispatch, me.id, st.data.match_id)) return;
      }
      if (st.ok && st.data?.status === "queued") {
        dispatch({ type: "SET_SCREEN", screen: "queue" });
        return;
      }
      dispatch({ type: "SET_SCREEN", screen: "lobby" });
    })();
  }, [dispatch]);
}
