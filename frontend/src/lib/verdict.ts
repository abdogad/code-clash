import type { Attempt } from "../state/types";

/** The note shown next to a verdict pill in an attempt row. */
export function attemptNote(a: Attempt): string {
  if (a.verdict === "AC") return "all tests passed";
  if (a.testsTotal) return `${a.testsPassed}/${a.testsTotal} tests passed`;
  return a.verdict.toLowerCase();
}
