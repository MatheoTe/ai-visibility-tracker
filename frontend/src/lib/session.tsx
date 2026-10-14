"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { api, getToken, onUnauthorized, setToken } from "@/lib/api/client";
import { meOut, tokenOut, type Workspace } from "@/lib/api/schemas";

interface Session {
  token: string | null;
  email: string | null;
  workspace: Workspace | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, workspaceName: string) => Promise<void>;
  logout: () => void;
}

const SessionContext = createContext<Session | null>(null);

const listeners = new Set<() => void>();
const subscribe = (cb: () => void) => {
  listeners.add(cb);
  return () => listeners.delete(cb);
};
const notify = () => listeners.forEach((cb) => cb());

export function SessionProvider({ children }: { children: ReactNode }) {
  const token = useSyncExternalStore(subscribe, getToken, () => null);
  const qc = useQueryClient();
  useEffect(() => {
    // An expired token anywhere signs the user out; the app shell then routes to /login.
    onUnauthorized(() => {
      qc.clear();
      notify();
    });
    return () => onUnauthorized(null);
  }, [qc]);
  const me = useQuery({
    queryKey: ["me", token],
    queryFn: () => api("/auth/me", meOut),
    enabled: !!token,
    staleTime: 60_000,
  });

  const authenticate = useCallback(
    async (path: string, body: object) => {
      const res = await api(path, tokenOut, { method: "POST", body });
      setToken(res.access_token);
      qc.clear();
      notify();
    },
    [qc],
  );

  const value = useMemo<Session>(
    () => ({
      token,
      email: me.data?.email ?? null,
      workspace: me.data?.workspaces[0] ?? null,
      loading: !!token && me.isPending,
      login: (email, password) => authenticate("/auth/login", { email, password }),
      register: (email, password, workspaceName) =>
        authenticate("/auth/register", { email, password, workspace_name: workspaceName }),
      logout: () => {
        setToken(null);
        qc.clear();
        notify();
      },
    }),
    [token, me.data, me.isPending, authenticate, qc],
  );
  return <SessionContext value={value}>{children}</SessionContext>;
}

export function useSession(): Session {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside <SessionProvider>");
  return ctx;
}
