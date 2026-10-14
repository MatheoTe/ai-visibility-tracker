import { Button } from "@/components/ui/button";

export function Pager({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number;
  limit: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  return (
    <div className="flex items-center justify-between pt-3 text-xs text-muted-foreground">
      <span className="tabular">
        {offset + 1}–{Math.min(offset + limit, total)} of {total.toLocaleString("en-US")}
      </span>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <Button variant="outline" size="sm" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          Next
        </Button>
      </div>
    </div>
  );
}
