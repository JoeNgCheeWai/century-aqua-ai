import type { ReactNode } from "react";
import StaffShell from "@/components/staff/StaffShell";
import { ADMIN_NAV } from "@/components/staff/nav";
import StaffGuard from "@/lib/staff-guard";

export const metadata = { title: "Command Centre | Century Aqua", robots: { index: false, follow: false } };

// Second lock (the proxy is the first). Nothing under /admin renders for non-staff.
export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <StaffGuard loginPath="/admin">
      <StaffShell
        title="Command Centre"
        badge="Operations"
        items={ADMIN_NAV}
        switchTo={{ href: "/internal", label: "Internal Workspace" }}
      >
        {children}
      </StaffShell>
    </StaffGuard>
  );
}
