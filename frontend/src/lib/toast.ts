/** Minimal app-wide toast store: one message at a time, auto-dismissed. */
export interface Toast {
  id: number;
  message: string;
}

let current: Toast | null = null;
let seq = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((cb) => cb());

export function showToast(message: string, durationMs = 5000): void {
  current = { id: ++seq, message };
  if (timer) clearTimeout(timer);
  timer = setTimeout(dismissToast, durationMs);
  emit();
}

export function dismissToast(): void {
  current = null;
  if (timer) clearTimeout(timer);
  timer = null;
  emit();
}

export const subscribeToast = (cb: () => void) => {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
};

export const getToast = (): Toast | null => current;
