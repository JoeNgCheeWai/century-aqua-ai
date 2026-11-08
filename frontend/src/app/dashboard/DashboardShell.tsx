"use client";

import { useState } from "react";
import type { Usage } from "@/lib/plans";
import ChatArea from "./ChatArea";
import Sidebar from "./Sidebar";

interface DashboardShellProps {
  /** Real usage read from the database on the server (null if it was unreachable). */
  initialUsage: Usage | null;
  firstName: string;
  userName: string;
}

export default function DashboardShell({ initialUsage, firstName, userName }: DashboardShellProps) {
  const [usage, setUsage] = useState<Usage | null>(initialUsage);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [chatKey, setChatKey] = useState(0);

  return (
    <div className="flex h-screen overflow-hidden bg-[#0A2342] text-white">
      {/* Sidebar: always visible on desktop, slide-over drawer on mobile. */}
      <div
        className={`fixed inset-y-0 left-0 z-40 transition-transform md:static md:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <Sidebar
          usage={usage}
          userName={userName}
          onNewChat={() => {
            setChatKey((k) => k + 1);
            setSidebarOpen(false);
          }}
          onClose={() => setSidebarOpen(false)}
        />
      </div>
      {sidebarOpen && (
        <button
          aria-label="Close sidebar"
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* A new key remounts the chat, which is how "New Chat" clears the conversation. */}
      <ChatArea
        key={chatKey}
        firstName={firstName}
        usage={usage}
        onUsage={setUsage}
        onOpenSidebar={() => setSidebarOpen(true)}
      />
    </div>
  );
}
