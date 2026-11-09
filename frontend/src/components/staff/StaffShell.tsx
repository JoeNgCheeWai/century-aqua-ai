"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserButton } from "@clerk/nextjs";

export interface StaffNavItem {
  href: string;
  label: string;
  /** SVG path (24x24 viewBox, stroke icon). */
  icon: string;
}

interface StaffShellProps {
  /** Shown under the logo, e.g. "Internal Workspace". */
  title: string;
  /** Small badge text, e.g. "Staff". */
  badge: string;
  items: StaffNavItem[];
  /** The other staff area, linked at the bottom of the sidebar. */
  switchTo: { href: string; label: string };
  children: ReactNode;
}

function Icon({ path }: { path: string }) {
  return (
    <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={path} />
    </svg>
  );
}

/** A nav link is active on its own page, and (except for the area root) on its sub-pages. */
function isActive(pathname: string, href: string, root: string): boolean {
  if (href === root) return pathname === root;
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** Sidebar + main area shared by /internal and /admin. Rendered only after the staff check passes. */
export default function StaffShell({ title, badge, items, switchTo, children }: StaffShellProps) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const root = items[0]?.href ?? "/";

  const sidebar = (
    <aside className="flex h-full w-64 flex-col border-r border-white/10 bg-[#081B35]">
      <div className="flex items-center justify-between px-4 pt-4">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold tracking-wide text-white">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#00C9A7] shadow-[0_0_10px_#00C9A7]" />
          CENTURY <span className="font-light text-[#00C9A7]">AQUA</span>
        </Link>
        <button onClick={() => setOpen(false)} aria-label="Close menu" className="text-white/50 transition hover:text-white md:hidden">✕</button>
      </div>

      <div className="px-4 pb-1 pt-4">
        <p className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">{title}</p>
        <span className="mt-2 inline-block rounded-full border border-[#00C9A7]/40 bg-[#00C9A7]/10 px-2.5 py-0.5 text-[11px] font-medium text-[#00C9A7]">
          {badge}
        </span>
      </div>

      <nav className="mt-3 min-h-0 flex-1 space-y-0.5 overflow-y-auto px-3" aria-label={title}>
        {items.map((item) => {
          const active = isActive(pathname, item.href, root);
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={() => setOpen(false)}
              aria-current={active ? "page" : undefined}
              className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${
                active ? "bg-[#00C9A7]/15 font-medium text-[#00C9A7]" : "text-white/70 hover:bg-white/5 hover:text-white"
              }`}
            >
              <Icon path={item.icon} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="space-y-3 border-t border-white/10 p-3">
        <Link href={switchTo.href} className="block rounded-lg px-3 py-2 text-xs text-white/50 transition hover:bg-white/5 hover:text-white">
          {switchTo.label} →
        </Link>
        <div className="flex items-center gap-3 px-1">
          <UserButton />
          <span className="text-xs text-white/50">Signed in as staff</span>
        </div>
      </div>
    </aside>
  );

  return (
    <div className="flex h-screen overflow-hidden bg-[#0A2342] text-white">
      <div className={`fixed inset-y-0 left-0 z-40 transition-transform md:static md:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
        {sidebar}
      </div>
      {open && <button aria-label="Close menu" className="fixed inset-0 z-30 bg-black/50 md:hidden" onClick={() => setOpen(false)} />}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-white/10 px-4 py-3 md:hidden">
          <button onClick={() => setOpen(true)} aria-label="Open menu" className="text-white/70 hover:text-white">☰</button>
          <span className="text-sm font-semibold">{title}</span>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
