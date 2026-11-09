import type { StaffNavItem } from "./StaffShell";

// 24x24 stroke-icon paths.
const ICON = {
  chat: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z",
  doc: "M6 3h9l4 4v14H6zM14 3v5h5",
  people: "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.5-2-3.4-2.3.9a7.5 7.5 0 0 0-2.2-1.3L14.4 3h-4l-.4 2.4a7.5 7.5 0 0 0-2.2 1.3l-2.3-.9-2 3.4 2 1.5a7.4 7.4 0 0 0 0 2.6l-2 1.5 2 3.4 2.3-.9a7.5 7.5 0 0 0 2.2 1.3l.4 2.4h4l.4-2.4a7.5 7.5 0 0 0 2.2-1.3l2.3.9 2-3.4-2-1.5c.1-.4.1-.9.1-1.3z",
  grid: "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
  chart: "M3 3v18h18M7 15l4-4 3 3 5-6",
  database: "M12 8c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3zM4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  pulse: "M22 12h-4l-3 9L9 3l-3 9H2",
  dollar: "M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6",
};

/** Internal Workspace sidebar. The first item is the area's home page. */
export const INTERNAL_NAV: StaffNavItem[] = [
  { href: "/internal", label: "New Chat", icon: ICON.chat },
  { href: "/internal/projects", label: "Projects", icon: ICON.folder },
  { href: "/internal/library", label: "Document Library", icon: ICON.doc },
  { href: "/internal/meetings", label: "Meeting Rooms", icon: ICON.people },
  { href: "/internal/settings", label: "Settings", icon: ICON.gear },
];

/** Command Centre sidebar. The first item is the area's home page. */
export const ADMIN_NAV: StaffNavItem[] = [
  { href: "/admin", label: "Overview", icon: ICON.grid },
  { href: "/admin/analytics", label: "Analytics", icon: ICON.chart },
  { href: "/admin/users", label: "Users", icon: ICON.people },
  { href: "/admin/rag", label: "RAG Management", icon: ICON.database },
  { href: "/admin/system", label: "System Health", icon: ICON.pulse },
  { href: "/admin/costs", label: "Costs", icon: ICON.dollar },
];
