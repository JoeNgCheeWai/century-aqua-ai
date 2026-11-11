"use client";

/**
 * Browser-side helpers for Mission Control. The browser only ever calls /api/mc/* (same origin, session cookie); the
 * server adds every backend key. A 401 means the session ended, so the page goes to the login screen and comes back.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { TERMINAL, type CouncilSession } from "./mc-types";

export class McError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** The most useful message in an error body: FastAPI's `detail`, or our `error`/`detail`. */
function messageOf(body: unknown, status: number): string {
  if (body && typeof body === "object") {
    const b = body as { detail?: unknown; error?: unknown };
    if (typeof b.detail === "string") return b.detail;
    if (Array.isArray(b.detail)) return "The request was not valid.";
    if (typeof b.error === "string") return b.error;
  }
  return `Request failed (${status}).`;
}

export async function mc<T>(path: string, init: { method?: "GET" | "POST"; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const response = await fetch(`/api/mc${path}`, {
    method: init.method ?? "GET",
    headers: { "Content-Type": "application/json" },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
    signal: init.signal,
  });
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // no JSON body
  }
  // Only OUR session check answers {error: "UNAUTHENTICATED"}. Any other 401 is not a reason to sign the user out.
  if (response.status === 401 && (body as { error?: unknown } | null)?.error === "UNAUTHENTICATED") {
    // A full navigation on purpose: it drops all client state when the session has ended.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(`/mission-control/login?next=${encodeURIComponent(window.location.pathname)}`);
    throw new McError(401, "Session ended. Signing in again…");
  }
  if (!response.ok) throw new McError(response.status, messageOf(body, response.status));
  return body as T;
}

/**
 * Load something once, and again whenever `deps` change or reload() is called. `error` is a readable message.
 * `loading` is true while the newest request is outstanding (it is derived from two counters, so no state is set
 * synchronously inside the effect). The previous data stays on screen while a reload is in flight.
 */
export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]) {
  const [tick, setTick] = useState(0);
  // The latest answer, tagged with the request it belongs to. Only ever set from a promise callback.
  const [result, setResult] = useState<{ key: string; data: T | null; error: string | null }>({ key: "", data: null, error: null });
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });

  // One key per request: it changes when a dependency changes or reload() is called.
  const requestKey = `${tick}|${JSON.stringify(deps)}`;

  useEffect(() => {
    const controller = new AbortController();
    loadRef
      .current(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setResult({ key: requestKey, data, error: null });
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted || (e as Error).name === "AbortError") return;
        // Keep the data already on screen and show the error beside it.
        setResult((previous) => ({ key: requestKey, data: previous.data, error: (e as Error).message }));
      });
    return () => controller.abort();
  }, [requestKey]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data: result.data, error: result.error, loading: result.key !== requestKey, reload };
}

/**
 * Poll one council session every `everyMs` until it reaches a terminal status (the async job pattern: POST returned 202,
 * the work runs in the background). Stops by itself, and on unmount. A failed poll is retried, up to `maxErrors` in a row.
 */
export function useCouncilPoll(sessionId: string | null, everyMs = 3000, maxErrors = 5) {
  // Tagged with the session it belongs to, so switching session never shows the previous one's data.
  const [state, setState] = useState<{ id: string | null; session: CouncilSession | null; error: string | null }>({
    id: null,
    session: null,
    error: null,
  });

  useEffect(() => {
    if (!sessionId) return;
    let stopped = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await mc<CouncilSession>(`/council/${sessionId}`);
        if (stopped) return;
        failures = 0;
        setState({ id: sessionId, session: next, error: null });
        if (TERMINAL.includes(next.status)) return;
      } catch (e) {
        if (stopped) return;
        failures += 1;
        setState((prev) => ({ id: sessionId, session: prev.id === sessionId ? prev.session : null, error: (e as Error).message }));
        if (failures >= maxErrors || (e instanceof McError && (e.status === 404 || e.status === 401))) return;
      }
      timer = setTimeout(poll, everyMs);
    };
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [sessionId, everyMs, maxErrors]);

  const current = state.id === sessionId;
  return { session: current ? state.session : null, error: current ? state.error : null };
}

/** "5m ago" style text for a UTC ISO time. */
export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function when(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "-";
}
