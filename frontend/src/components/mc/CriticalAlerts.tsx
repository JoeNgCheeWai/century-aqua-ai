"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { mc } from "@/lib/mc-client";
import type { NotificationList } from "@/lib/mc-types";

const EVERY_MS = 30_000;

/**
 * How many critical decisions the COO has escalated to the CEO that are still unread. `null` means "not known" (not loaded yet, or the backend
 * could not say): it is never shown as zero, because a zero would tell the CEO that nothing needs them.
 *
 * This is the ONLY way a notification reaches the CEO: it is in-app, so it appears while Mission Control is open (it checks every 30 seconds while the
 * tab is visible). There is no email, text or push.
 */
export function useUnreadCritical(): number | null {
  const [unread, setUnread] = useState<number | null>(null);
  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const list = await mc<NotificationList>("/ceo-notifications?status=unread&limit=1");
        if (!stopped) setUnread(list.unread);
      } catch {
        if (!stopped) setUnread(null);
      }
    };
    poll();
    const timer = setInterval(poll, EVERY_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);
  return unread;
}

/** The red strip above every page while a critical decision is unread. */
export function CriticalBanner({ unread }: { unread: number | null }) {
  if (!unread) return null;
  return (
    <div role="alert" className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-red-400/40 bg-red-500/10 px-4 py-3 text-sm text-red-100">
      <span>
        <strong>
          {unread} critical {unread === 1 ? "decision needs" : "decisions need"} you.
        </strong>{" "}
        The COO has explained why in plain words.
      </span>
      <Link href="/mission-control/authorizations" className="rounded-lg bg-red-500 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-red-400">
        Read and decide
      </Link>
    </div>
  );
}

/** The small red count beside a menu item. */
export function CountBadge({ count }: { count: number | null }) {
  if (!count) return null;
  return (
    <span aria-label={`${count} unread`} className="ml-auto inline-block min-w-5 rounded-full bg-red-500 px-1.5 text-center text-[11px] font-semibold text-white">
      {count > 99 ? "99+" : count}
    </span>
  );
}
