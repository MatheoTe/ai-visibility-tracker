import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { Skeleton } from "@/components/ui/skeleton";

// The app is client-rendered behind auth: the static shell is just this fallback,
// and everything under it streams in once the session is known.
export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <Suspense
      fallback={
        <div className="mx-auto max-w-6xl space-y-4 p-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-64 w-full" />
        </div>
      }
    >
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
