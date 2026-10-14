import { Suspense } from "react";

import { PromptDetail } from "@/components/prompt-detail";
import { Skeleton } from "@/components/ui/skeleton";

async function Loader({ params }: { params: Promise<{ promptId: string }> }) {
  const { promptId } = await params;
  return <PromptDetail promptId={promptId} />;
}

export default function PromptPage({ params }: PageProps<"/prompts/[promptId]">) {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <Loader params={params} />
    </Suspense>
  );
}
