/* useDuelSocket — the live duel connection. The websocket is the source of truth:
   this hook parses the server's {seq, type, payload} envelopes and dispatches
   INGEST_EVENT, leaving interpretation to the reducer (it only does seq-dedupe and
   reconnect).

   Endpoint /ws/duel/{matchId}?last_seq=<seq>:
   - Auth rides the httpOnly access cookie on the same-origin handshake (no token in
     the URL). If expired the server closes 4401; we refresh and reconnect, or sign
     out if the session is gone.
   - Reconnect carries last_seq so the server replays the gap (survives reloads and a
     backend restart). A live event may arrive twice during replay, so we dedupe by
     seq here. */

import { useEffect, useRef } from "react";
import type { Dispatch } from "react";
import type { Action } from "../state/duelMachine";
import type { WsEnvelope } from "../state/types";
import { auth, refreshSession } from "./api";

const WS_AUTH_CLOSE = 4401; // server rejected the access cookie (expired/invalid)

const RECONNECT_MS = 2000;

export function useDuelSocket(
  matchId: string | null,
  dispatch: Dispatch<Action>,
): void {
  // lastSeq persists across reconnects within a single mounted duel.
  const lastSeq = useRef(0);

  useEffect(() => {
    // Authenticated socket: without a session there's nothing to connect to.
    if (!matchId || !auth.isAuthed) return;
    lastSeq.current = 0;

    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false; // set on unmount so we stop reconnecting

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${location.host}/ws/duel/${matchId}?last_seq=${lastSeq.current}`;
      ws = new WebSocket(url);

      ws.onmessage = (m) => {
        const event = JSON.parse(m.data) as WsEnvelope;
        // Cosmetic, broadcast-only events carry seq=0 and live outside the
        // durable log: they bypass seq-dedupe and never advance lastSeq.
        if (event.type === "judging_progress") {
          dispatch({ type: "INGEST_EVENT", event });
          return;
        }
        if (event.seq <= lastSeq.current) return; // dedupe replay overlap
        lastSeq.current = event.seq;
        dispatch({ type: "INGEST_EVENT", event });
      };

      ws.onclose = (ev) => {
        if (closed) return;
        // Access cookie expired mid-duel: refresh once, then reconnect. If the
        // session is genuinely gone, sign out instead of retry-spamming a 4401.
        if (ev.code === WS_AUTH_CLOSE) {
          void refreshSession().then((ok) => {
            if (closed) return;
            if (ok) connect();
            else {
              auth.clear();
              window.dispatchEvent(new Event("auth-expired"));
            }
          });
          return;
        }
        retry = setTimeout(connect, RECONNECT_MS);
      };
    };

    connect();

    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
  }, [matchId, dispatch]);
}
