"use client";

import { Suspense, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/mission-control/dashboard", label: "Dashboard", icon: "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" },
  { href: "/mission-control/agents/cao", label: "Agents", icon: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" },
  { href: "/mission-control/council", label: "Council", icon: "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" },
  { href: "/mission-control/authorizations", label: "Authorizations", icon: "M9 12l2 2 4-4M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" },
  { href: "/mission-control/audit", label: "Audit log", icon: "M6 3h9l4 4v14H6zM14 3v5h5M9 13h6M9 17h6" },
];

function Icon({ path }: { path: string }) {
  return (
    <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={path} />
    </svg>
  );
}

const LINK_BASE = "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition";

/**
 * The nav links. The active link depends on the current URL, which is only known at request time (Cache Components), so
 * this is the one part of the shell that sits behind <Suspense>; the rest of the shell prerenders.
 */
function NavLinks({ onNavigate }: { onNavigate: () => void }) {
  const pathname = usePathname();
  return (
    <>
      {NAV.map((item) => {
        const section = item.href.split("/").slice(0, 3).join("/");
        const active = pathname === item.href || pathname.startsWith(`${section}/`) || pathname === section;
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={`${LINK_BASE} ${active ? "bg-cyan-400/15 font-medium text-cyan-200" : "text-slate-400 hover:bg-white/5 hover:text-white"}`}
          >
            <Icon path={item.icon} />
            {item.label}
          </Link>
        );
      })}
    </>
  );
}

/** The same links with no active state, shown for the instant before the URL is known. */
function PlainNavLinks() {
  return (
    <>
      {NAV.map((item) => (
        <Link key={item.href} href={item.href} className={`${LINK_BASE} text-slate-400 hover:bg-white/5 hover:text-white`}>
          <Icon path={item.icon} />
          {item.label}
        </Link>
      ))}
    </>
  );
}

/** Sidebar + main area for every Mission Control page except login. */
export default function McShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  async function logout() {
    await fetch("/api/mc/logout", { method: "POST" });
    // A full navigation on purpose: it drops all client state once the cookie is cleared.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/mission-control/login");
  }

  const sidebar = (
    <aside className="flex h-full w-60 flex-col border-r border-cyan-400/15 bg-slate-950">
      <div className="px-4 pt-4">
        <Link href="/mission-control/dashboard" className="flex items-center gap-2 text-sm font-semibold tracking-wide text-white">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-cyan-400 shadow-[0_0_12px_#22d3ee]" />
          MISSION <span className="font-light text-cyan-300">CONTROL</span>
        </Link>
        <p className="mt-1 text-[11px] uppercase tracking-[0.2em] text-slate-500">Century Aqua · Executive team</p>
      </div>
      <nav className="mt-4 min-h-0 flex-1 space-y-0.5 overflow-y-auto px-3" aria-label="Mission Control">
        <Suspense fallback={<PlainNavLinks />}>
          <NavLinks onNavigate={() => setOpen(false)} />
        </Suspense>
      </nav>
      <div className="border-t border-cyan-400/15 p-3">
        <button onClick={logout} className="w-full rounded-lg px-3 py-2 text-left text-xs text-slate-400 transition hover:bg-white/5 hover:text-white">
          Sign out
        </button>
      </div>
    </aside>
  );

  return (
    <div className="flex h-screen overflow-hidden bg-[radial-gradient(ellipse_at_top,_#0b1b3a_0%,_#020617_60%)] text-slate-100">
      <div className={`fixed inset-y-0 left-0 z-40 transition-transform md:static md:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>{sidebar}</div>
      {open && <button aria-label="Close menu" className="fixed inset-0 z-30 bg-black/60 md:hidden" onClick={() => setOpen(false)} />}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-cyan-400/15 px-4 py-3 md:hidden">
          <button onClick={() => setOpen(true)} aria-label="Open menu" className="text-slate-300 hover:text-white">☰</button>
          <span className="text-sm font-semibold">Mission Control</span>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
