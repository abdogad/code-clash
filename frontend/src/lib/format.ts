/** seconds -> "m:ss" (used in lists / hints). */
export const clock = (s: number): string =>
  `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** seconds -> "mm:ss" (the big duel timer). */
export const clock2 = (s: number): string =>
  `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(
    Math.floor(s % 60),
  ).padStart(2, "0")}`;

/** first letter of a name, lowercased — the avatar glyph. */
export const initial = (name?: string): string =>
  name ? name[0].toLowerCase() : "?";

/** rating -> "Silver II" style tier (divisions every 100 within a 300 band). */
export function tierFor(rating: number): string {
  const tiers = [
    { min: 2000, name: "Diamond" },
    { min: 1700, name: "Platinum" },
    { min: 1400, name: "Gold" },
    { min: 1100, name: "Silver" },
    { min: -Infinity, name: "Bronze" },
  ];
  const t = tiers.find((x) => rating >= x.min)!;
  const base = Number.isFinite(t.min) ? t.min : 800;
  const div = ["III", "II", "I"][
    Math.min(2, Math.max(0, Math.floor((rating - base) / 100)))
  ];
  return `${t.name} ${div}`;
}

/** ISO timestamp -> coarse relative time ("2h ago", "yesterday"). */
export function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const sec = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  if (sec < 172800) return "yesterday";
  return `${Math.floor(sec / 86400)}d ago`;
}
