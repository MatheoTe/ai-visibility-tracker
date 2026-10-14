import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HighlightedText } from "@/components/highlighted-text";
import { KpiCard } from "@/components/kpi-card";

describe("HighlightedText", () => {
  it("marks each detected mention with its brand", () => {
    const text = "1. **Pipewise** - great. 2. Lumora CRM - solid.";
    render(
      <HighlightedText
        text={text}
        colors={new Map([["l", "var(--series-1)"]])}
        mentions={[
          { brand_id: "l", brand: "Lumora CRM", position: 2, sentiment: "positive", score: 0.5, spans: [{ start: 28, end: 38, kind: "exact" }] },
          { brand_id: "p", brand: "Pipewise", position: 1, sentiment: "neutral", score: 0, spans: [{ start: 5, end: 13, kind: "exact" }] },
        ]}
      />,
    );
    const marks = screen.getAllByRole("mark");
    expect(marks.map((m) => m.textContent)).toEqual(["Pipewise", "Lumora CRM"]);
    expect(marks[1]).toHaveAttribute("data-brand", "Lumora CRM");
    expect(screen.getByText(/great\. 2\./)).toBeInTheDocument();
  });
});

describe("KpiCard", () => {
  it("shows value, delta and hint", () => {
    render(<KpiCard label="Share of voice" value="31.2%" delta="+2.4 pp" trend="up" hint="vs previous period" />);
    expect(screen.getByText("31.2%")).toBeInTheDocument();
    expect(screen.getByText("+2.4 pp")).toBeInTheDocument();
    expect(screen.getByText("vs previous period")).toBeInTheDocument();
  });
});
