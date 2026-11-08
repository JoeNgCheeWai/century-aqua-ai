"use client";

import Link from "next/link";
import { UserButton } from "@clerk/nextjs";
import type { Usage } from "@/lib/plans";
import { MOCK_CHATS, NAV_ITEMS } from "./mock-data";
import UsageBadge from "./UsageBadge";

function Icon({ path }: { path: string }) {
  return (
    <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  );
}

interface SidebarProps {
  usage: Usage | null;
  userName: string;
  onNewChat: () => void;
  onClose: () => void;
}

export default function Sidebar({ usage, userName, onNewChat, onClose }: SidebarProps) {
  return (
    <aside className="flex h-full w-72 flex-col border-r border-white/10 bg-[#081B35]">
      <div className="flex items-center justify-between px-4 pt-4">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold tracking-wide text-white">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#00C9A7] shadow-[0_0_10px_#00C9A7]" />
          CENTURY <span className="font-light text-[#00C9A7]">AQUA</span>
        </Link>
        <button onClick={onClose} aria-label="Close sidebar" className="text-white/50 transition hover:text-white md:hidden">✕</button>
      </div>

      <div className="px-3 pt-4">
        <button
          onClick={onNewChat}
          className="flex w-full items-center gap-2 rounded-lg border border-[#00C9A7]/40 bg-[#00C9A7]/10 px-3 py-2 text-sm font-medium text-[#00C9A7] transition hover:bg-[#00C9A7]/20"
        >
          <Icon path="M12 5v14M5 12h14" />
          New Chat
        </button>
      </div>

      <nav className="mt-4 space-y-0.5 px-3" aria-label="Dashboard sections">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            title="Coming soon"
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-white/70 transition hover:bg-white/5 hover:text-white"
          >
            <Icon path={item.icon} />
            <span className="flex-1">{item.label}</span>
            <span className="text-[10px] uppercase tracking-wider text-white/30">Soon</span>
          </button>
        ))}
      </nav>

      <div className="mt-5 min-h-0 flex-1 overflow-y-auto px-3">
        <p className="px-3 pb-2 text-[11px] font-medium uppercase tracking-widest text-white/30">Recent (sample)</p>
        <ul className="space-y-0.5">
          {MOCK_CHATS.map((chat) => (
            <li key={chat.id}>
              <button type="button" className="w-full truncate rounded-lg px-3 py-1.5 text-left text-sm text-white/60 transition hover:bg-white/5 hover:text-white">
                {chat.title}
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="space-y-3 border-t border-white/10 p-3">
        <UsageBadge usage={usage} />
        <div className="flex items-center gap-3 px-1">
          <UserButton />
          <span className="truncate text-sm text-white/70">{userName}</span>
        </div>
      </div>
    </aside>
  );
}
