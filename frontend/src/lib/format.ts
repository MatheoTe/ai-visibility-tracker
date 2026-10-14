export const pct = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;

/** Percentage-point delta with an explicit sign, e.g. "+2.4 pp". */
export const ppDelta = (v: number | null | undefined): string | null => {
  if (v === null || v === undefined) return null;
  const pp = v * 100;
  if (Math.abs(pp) < 0.05) return "±0.0 pp";
  return `${pp > 0 ? "+" : "−"}${Math.abs(pp).toFixed(1)} pp`;
};

export const position = (v: number | null | undefined): string => (v === null || v === undefined ? "—" : `#${v.toFixed(1)}`);

export const sentiment = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}`;

export const usd = (v: string | number): string => {
  const n = typeof v === "string" ? Number(v) : v;
  return n < 1 ? `$${n.toFixed(4)}` : `$${n.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
};

export const int = (v: number): string => v.toLocaleString("en-US");

/** "anthropic:claude-sonnet-5-5" -> "claude-sonnet-5-5" */
export const modelLabel = (key: string): string => key.split(":").slice(1).join(":") || key;

export const shortDay = (iso: string): string => {
  const [, m, d] = iso.split("-");
  return `${Number(m)}/${Number(d)}`;
};

export const relativeTime = (iso: string, now: Date = new Date()): string => {
  const secs = Math.round((now.getTime() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
};
