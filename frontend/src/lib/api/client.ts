import type { z } from "zod";

import { showToast } from "@/lib/toast";

import { createDemoBackend, DEMO_MODE, DemoReadOnlyError, fetchDemoFile } from "./demo";
import { ApiError } from "./errors";

export { ApiError };

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "avt.token";
const DEMO_TOKEN = "demo";

// The static live demo answers from a committed snapshot instead of the backend.
const demoBackend = DEMO_MODE ? createDemoBackend(fetchDemoFile) : null;

export function getToken(): string | null {
  if (DEMO_MODE) return DEMO_TOKEN; // always signed in as the fictional demo user
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** Token seen while prerendering/hydrating, before localStorage can be read. */
export const getServerToken = (): string | null => (DEMO_MODE ? DEMO_TOKEN : null);

export function setToken(token: string | null): void {
  if (DEMO_MODE) return;
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage unavailable (private mode): the session just won't persist
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Called when an authenticated request comes back 401 (expired or revoked token). */
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

type Init = Omit<RequestInit, "body"> & { body?: unknown; form?: FormData };

/** Fetch an API path and validate the JSON body with `schema`. */
export async function api<T extends z.ZodType>(path: string, schema: T, init: Init = {}): Promise<z.infer<T>> {
  if (demoBackend) {
    try {
      return schema.parse(await demoBackend.request(path, init.method));
    } catch (err) {
      if (err instanceof DemoReadOnlyError) showToast(err.message);
      throw err;
    }
  }
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body: BodyInit | undefined;
  if (init.form) body = init.form;
  else if (init.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.body);
  }
  const res = await fetch(`${API_URL}/api/v1${path}`, { ...init, headers, body });
  if (res.status === 401 && token && !path.startsWith("/auth/login")) {
    setToken(null);
    unauthorizedHandler?.();
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) detail = data.detail.map((d: { msg?: string }) => d.msg).join("; ");
    } catch {
      // non-JSON error body
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return schema.parse(undefined);
  return schema.parse(await res.json());
}
