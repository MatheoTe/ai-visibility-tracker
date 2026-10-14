import { Suspense } from "react";

import { RunDetail } from "@/components/run-detail";
import { Skeleton } from "@/components/ui/skeleton";

async function Loader({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return <RunDetail runId={runId} />;
}

export default function RunPage({ params }: PageProps<"/runs/[runId]">) {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <Loader params={params} />
    </Suspense>
  );
}
