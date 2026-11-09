import type { ReactNode } from "react";
import StaffShell from "@/components/staff/StaffShell";
import { INTERNAL_NAV } from "@/components/staff/nav";
import StaffGuard from "@/lib/staff-guard";

export const metadata = { title: "Internal Workspace | Century Aqua", robots: { index: false, follow: false } };

// Second lock (the proxy is the first). Nothing under /internal renders for non-staff.
export default function InternalLayout({ children }: { children: ReactNode }) {
  return (
    <StaffGuard loginPath="/internal">
      <StaffShell
        title="Internal Workspace"
        badge="Staff"
        items={INTERNAL_NAV}
        switchTo={{ href: "/admin", label: "Command Centre" }}
      >
        {children}
      </StaffShell>
    </StaffGuard>
  );
}
