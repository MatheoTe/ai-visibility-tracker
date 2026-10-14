import type { ResponseMention } from "@/lib/api/schemas";
import { segment } from "@/lib/highlight";

/** Renders an AI answer with every detected brand mention highlighted in its series color. */
export function HighlightedText({
  text,
  mentions,
  colors,
}: {
  text: string;
  mentions: ResponseMention[];
  colors: Map<string, string>;
}) {
  const spans = mentions.flatMap((m) => m.spans.map((s) => ({ start: s.start, end: s.end, brandId: m.brand_id })));
  const names = new Map(mentions.map((m) => [m.brand_id, m.brand]));
  return (
    <p className="whitespace-pre-wrap text-sm leading-relaxed">
      {segment(text, spans).map((seg, i) =>
        seg.brandId ? (
          <mark
            key={i}
            title={names.get(seg.brandId)}
            data-brand={names.get(seg.brandId)}
            className="rounded px-0.5 font-medium text-foreground"
            style={{
              background: `color-mix(in oklab, ${colors.get(seg.brandId) ?? "var(--mark)"} 22%, transparent)`,
              boxShadow: `inset 0 -2px 0 ${colors.get(seg.brandId) ?? "var(--mark)"}`,
            }}
          >
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </p>
  );
}
