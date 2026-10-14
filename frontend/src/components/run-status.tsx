import { Badge } from "@/components/ui/badge";

const VARIANT: Record<string, "good" | "warning" | "critical" | "info" | "default"> = {
  completed: "good",
  partial: "warning",
  failed: "critical",
  running: "info",
  pending: "default",
  succeeded: "good",
  dead: "critical",
  queued: "default",
};

export function RunStatus({ status }: { status: string }) {
  return <Badge variant={VARIANT[status] ?? "default"}>{status}</Badge>;
}

export function ProgressBar({ value, label }: { value: number; label: string }) {
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-muted"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value)}
    >
      <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${value}%` }} />
    </div>
  );
}
