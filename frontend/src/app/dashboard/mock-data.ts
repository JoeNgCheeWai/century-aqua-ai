// MOCK DATA for the dashboard skeleton. Replace with real queries when the
// Projects, Document Library and chat-history features are built.

export interface MockChat {
  id: string;
  title: string;
  when: string;
}

export const MOCK_CHATS: MockChat[] = [
  { id: "c1", title: "Ammonia spike in tilapia pond", when: "Today" },
  { id: "c2", title: "Biofloc carbon source options", when: "Today" },
  { id: "c3", title: "Stocking density for RAS nursery", when: "Yesterday" },
  { id: "c4", title: "SND Probiotic: how it works", when: "Yesterday" },
  { id: "c5", title: "Feed conversion ratio review", when: "This week" },
  { id: "c6", title: "Dissolved oxygen alarm setup", when: "This week" },
];

export const SUGGESTIONS: string[] = [
  "How do I manage ammonia in a tilapia pond?",
  "What stocking density suits a RAS nursery?",
  "Explain simultaneous nitrification and denitrification.",
];

export interface NavItem {
  id: "projects" | "library" | "settings";
  label: string;
  /** Path of the 16x16 icon, drawn in Sidebar. */
  icon: string;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "projects", label: "Projects", icon: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" },
  { id: "library", label: "Document Library", icon: "M6 3h9l4 4v14H6zM14 3v5h5" },
  { id: "settings", label: "Settings", icon: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.5-2-3.4-2.3.9a7.5 7.5 0 0 0-2.2-1.3L14.4 3h-4l-.4 2.4a7.5 7.5 0 0 0-2.2 1.3l-2.3-.9-2 3.4 2 1.5a7.4 7.4 0 0 0 0 2.6l-2 1.5 2 3.4 2.3-.9a7.5 7.5 0 0 0 2.2 1.3l.4 2.4h4l.4-2.4a7.5 7.5 0 0 0 2.2-1.3l2.3.9 2-3.4-2-1.5c.1-.4.1-.9.1-1.3z" },
];
