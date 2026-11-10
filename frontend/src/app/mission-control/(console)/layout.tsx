import type { ReactNode } from "react";
import McShell from "@/components/mc/McShell";

export const metadata = { title: "Mission Control | Century Aqua", robots: { index: false, follow: false } };

// The proxy (src/proxy.ts) already refused anyone without a valid session; every /api/mc handler checks again.
// The login page is NOT in this route group, so it has no sidebar.
export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return <McShell>{children}</McShell>;
}
