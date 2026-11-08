"use client";

import { useEffect, useState } from "react";
import { ROLE_LABELS, type Usage } from "@/lib/plans";

function formatRemaining(ms: number): string {
  const minutes = Math.max(1, Math.ceil(ms / 60_000));
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/**
 * The "Messages Remaining: X/100" badge. `usage` comes from the real database
 * (null means the database could not be reached).
 */
export default function UsageBadge({ usage }: { usage: Usage | null }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  if (!usage) {
    return (
      <div className="rounded-xl border border-white/10 bg-white/5 p-3 text-xs text-white/60">
        Messages Remaining: unavailable
        <p className="mt-1 text-white/40">Usage could not be loaded. Try refreshing.</p>
      </div>
    );
  }

  if (usage.unlimited) {
    return (
      <div className="rounded-xl border border-[#00C9A7]/30 bg-[#00C9A7]/10 p-3 text-xs">
        <p className="font-medium text-[#00C9A7]">Messages Remaining: Unlimited</p>
        <p className="mt-1 text-white/50">{ROLE_LABELS[usage.role]}</p>
      </div>
    );
  }

  const limit = usage.limit ?? 0;
  const remaining = usage.remaining ?? 0;
  const percent = limit > 0 ? Math.round((remaining / limit) * 100) : 0;
  const low = remaining <= Math.ceil(limit * 0.1);
  const resetMs = usage.resetTime ? new Date(usage.resetTime).getTime() - now : null;

  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-3 text-xs">
      <div className="flex items-baseline justify-between">
        <span className="text-white/60">Messages Remaining</span>
        <span className={`text-sm font-semibold ${low ? "text-amber-300" : "text-[#00C9A7]"}`}>
          {remaining}/{limit}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10">
        <div
          className={`h-full rounded-full transition-all duration-500 ${low ? "bg-amber-300" : "bg-[#00C9A7]"}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <p className="mt-2 text-white/40" suppressHydrationWarning>
        {ROLE_LABELS[usage.role]}
        {resetMs !== null && resetMs > 0 ? ` · resets in ${formatRemaining(resetMs)}` : ""}
      </p>
    </div>
  );
}
