"use client";

import { ExternalLink, X } from "lucide-react";
import { useSyncExternalStore } from "react";

import { DEMO_REPO_URL } from "@/lib/api/demo";
import { dismissToast, getToast, subscribeToast } from "@/lib/toast";

/** Thin strip above the app explaining what the live demo is. */
export function DemoBanner() {
  return (
    <div
      role="note"
      className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b border-border bg-primary/10 px-4 py-1.5 text-center text-xs"
    >
      <span>
        <span className="font-medium">Live demo</span> · fictional data · read-only
      </span>
      <a
        href={DEMO_REPO_URL}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
      >
        Source &amp; full system <ExternalLink className="size-3" aria-hidden />
      </a>
    </div>
  );
}

/** Renders the current toast from `lib/toast` (bottom right, polite live region). */
export function Toaster() {
  const toast = useSyncExternalStore(subscribeToast, getToast, () => null);
  return (
    <div aria-live="polite" className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex justify-end">
      {toast ? (
        <div
          key={toast.id}
          role="status"
          className="pointer-events-auto flex max-w-sm items-start gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm text-card-foreground shadow-lg"
        >
          <span>{toast.message}</span>
          <button
            type="button"
            onClick={dismissToast}
            aria-label="Dismiss"
            className="-mr-1 shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>
      ) : null}
    </div>
  );
}
