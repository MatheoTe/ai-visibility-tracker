"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { DEMO_MODE } from "@/lib/api/demo";
import { useSession } from "@/lib/session";

export default function LoginPage() {
  const { login, register } = useSession();
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("demo@avt.dev");
  const [password, setPassword] = useState("demo-password");
  const [workspace, setWorkspace] = useState("My brand");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The live demo is always signed in as the fictional demo user.
  useEffect(() => {
    if (DEMO_MODE) router.replace("/dashboard");
  }, [router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, workspace);
      router.push(mode === "login" ? "/dashboard" : "/brands");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-lg">AI Visibility Tracker</CardTitle>
          <CardDescription>
            {mode === "login"
              ? "Sign in. The demo account is pre-filled."
              : "Create an account and your first workspace."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </div>
            <div className="space-y-1">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {mode === "register" ? (
              <div className="space-y-1">
                <Label htmlFor="workspace">Workspace name</Label>
                <Input id="workspace" value={workspace} onChange={(e) => setWorkspace(e.target.value)} required />
              </div>
            ) : null}
            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
            </Button>
          </form>
          <button
            type="button"
            className="mt-4 w-full text-center text-xs text-muted-foreground hover:text-foreground"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              if (mode === "login") {
                setEmail("");
                setPassword("");
              }
            }}
          >
            {mode === "login" ? "No account? Create one" : "Have an account? Sign in"}
          </button>
        </CardContent>
      </Card>
    </div>
  );
}
