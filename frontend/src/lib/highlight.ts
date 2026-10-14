export interface HighlightSpan {
  start: number;
  end: number;
  brandId: string;
}

export interface Segment {
  text: string;
  brandId: string | null;
}

/**
 * Split `text` into plain and highlighted segments. Spans come from the backend's
 * detector (offsets into the original text); overlapping or out-of-range spans are
 * clipped so a bad span can never duplicate or drop characters.
 */
export function segment(text: string, spans: HighlightSpan[]): Segment[] {
  const sorted = [...spans]
    .filter((s) => s.end > s.start)
    .map((s) => ({ ...s, start: Math.max(0, s.start), end: Math.min(text.length, s.end) }))
    .sort((a, b) => a.start - b.start);
  const out: Segment[] = [];
  let cursor = 0;
  for (const s of sorted) {
    if (s.start < cursor) continue; // overlapping span: keep the earlier one
    if (s.start > cursor) out.push({ text: text.slice(cursor, s.start), brandId: null });
    out.push({ text: text.slice(s.start, s.end), brandId: s.brandId });
    cursor = s.end;
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor), brandId: null });
  return out;
}
