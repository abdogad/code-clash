/* Loads a match (GET /api/matches/{id}) and dispatches ENTER_DUEL. Shared by
   the match-found reveal and the boot reconnect-into-active-duel path. Returns
   false if the match is gone or already finished. */
import type { Dispatch } from "react";
import type { Action } from "../state/duelMachine";
import { api, type MatchResponse } from "./api";

export async function enterMatch(
  dispatch: Dispatch<Action>,
  meId: number,
  matchId: string,
  pre?: MatchResponse,
): Promise<boolean> {
  const m = pre ?? (await api<MatchResponse>(`/matches/${matchId}`)).data;
  if (!m || m.state === "FINISHED") return false;
  const oppId = Number(
    Object.keys(m.players).find((id) => Number(id) !== meId),
  );
  dispatch({
    type: "ENTER_DUEL",
    matchId,
    oppId,
    startedAt: new Date(m.started_at).getTime(),
    players: m.players,
    problem: m.problem,
  });
  return true;
}
