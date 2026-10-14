import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface Props {
  label: string;
  value: string;
  delta?: string | null;
  /** Direction of the delta, and whether up is good for this metric. */
  trend?: "up" | "down" | "flat" | null;
  hint?: string;
}

export function KpiCard({ label, value, delta, trend, hint }: Props) {
  const Icon = trend === "up" ? ArrowUpRight : trend === "down" ? ArrowDownRight : Minus;
  return (
    <Card className="p-4">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="tabular mt-1 text-2xl font-semibold tracking-tight">{value}</div>
      <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
        {delta ? (
          <span
            className={cn(
              "inline-flex items-center gap-0.5 font-medium",
              trend === "up" && "text-status-good",
              trend === "down" && "text-status-critical",
            )}
          >
            <Icon className="size-3" aria-hidden />
            {delta}
          </span>
        ) : null}
        {hint ? <span>{hint}</span> : null}
      </div>
    </Card>
  );
}
