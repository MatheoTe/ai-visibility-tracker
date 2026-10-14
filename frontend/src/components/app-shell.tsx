"use client";

import { Activity, BarChart3, Globe, LogOut, MessageSquareText, Tags } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { DEMO_MODE } from "@/lib/api/demo";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Overview", icon: BarChart3 },
  { href: "/prompts", label: "Prompts", icon: MessageSquareText },
  { href: "/brands", label: "Brands", icon: Tags },
  { href: "/sources", label: "Sources", icon: Globe },
  { href: "/runs", label: "Runs", icon: Activity },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { token, workspace, email, loading, logout } = useSession();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!token) router.replace("/login");
  }, [token, router]);

  if (!token || loading || !workspace) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col gap-1 border-b border-border bg-card px-3 py-3 md:w-56 md:border-b-0 md:border-r md:py-5">
        <div className="mb-3 hidden px-2 md:block">
          <div className="text-sm font-semibold">Echolytics</div>
          <div className="truncate text-xs text-muted-foreground" title={workspace.name}>
            {workspace.name}
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto md:flex-col">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                  active && "bg-muted font-medium text-foreground",
                )}
              >
                <Icon className="size-4" />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto hidden px-2 pt-4 md:block">
          <div className="mb-2 truncate text-xs text-muted-foreground">{email}</div>
          {DEMO_MODE ? null : (
            <Button variant="ghost" size="sm" onClick={logout} className="-ml-2">
              <LogOut /> Sign out
            </Button>
          )}
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8">{children}</main>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
