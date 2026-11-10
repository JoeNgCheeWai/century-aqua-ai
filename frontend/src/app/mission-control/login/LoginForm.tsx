"use client";

import { useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { buttonPrimary, inputClass } from "@/components/mc/ui";

export default function LoginForm() {
  const params = useSearchParams();
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/mc/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin, next: params.get("next") }),
      });
      if (response.ok) {
        // A full navigation, so the new cookie is sent with the very next request. The server vetted the target.
        const { next } = (await response.json()) as { next?: string };
        window.location.assign(next || "/mission-control/dashboard");
        return;
      }
      const body = (await response.json().catch(() => ({}))) as { error?: string; retryAfterSeconds?: number };
      if (response.status === 401) setError("That PIN is not right.");
      else if (response.status === 429) setError(`Too many attempts. Try again in ${Math.ceil((body.retryAfterSeconds ?? 60) / 60)} min.`);
      else if (response.status === 503) setError("Login is not set up on the server (ADMIN_PIN and ADMIN_SESSION_SECRET).");
      else setError("Could not sign in. Try again.");
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
      setPin("");
    }
  }

  return (
    <form onSubmit={submit} className="mt-6 space-y-4">
      <label className="block text-left text-xs uppercase tracking-[0.18em] text-cyan-300/80" htmlFor="pin">
        Access PIN
      </label>
      <input
        id="pin"
        type="password"
        inputMode="numeric"
        autoComplete="current-password"
        autoFocus
        value={pin}
        onChange={(e) => setPin(e.target.value)}
        className={`${inputClass} text-center font-mono text-lg tracking-[0.4em]`}
        aria-describedby={error ? "pin-error" : undefined}
      />
      {error && (
        <p id="pin-error" role="alert" className="text-sm text-red-300">
          {error}
        </p>
      )}
      <button type="submit" disabled={busy || pin.length === 0} className={`${buttonPrimary} w-full`}>
        {busy ? "Checking…" : "Enter Mission Control"}
      </button>
    </form>
  );
}
