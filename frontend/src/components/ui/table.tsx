import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

export function Table({ className, ...props }: ComponentProps<"table">) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full caption-bottom text-sm", className)} {...props} />
    </div>
  );
}
export const THead = (p: ComponentProps<"thead">) => <thead {...p} className={cn("[&_tr]:border-b", p.className)} />;
export const TBody = (p: ComponentProps<"tbody">) => <tbody {...p} className={cn("[&_tr:last-child]:border-0", p.className)} />;
export const Tr = (p: ComponentProps<"tr">) => (
  <tr {...p} className={cn("border-b border-border transition-colors hover:bg-muted/40", p.className)} />
);
export const Th = (p: ComponentProps<"th">) => (
  <th {...p} className={cn("h-9 px-3 text-left align-middle text-xs font-medium text-muted-foreground", p.className)} />
);
export const Td = (p: ComponentProps<"td">) => <td {...p} className={cn("px-3 py-2.5 align-middle", p.className)} />;
