import { Suspense } from "react";
import AuditBrowser from "./AuditBrowser";

export const metadata = { title: "Audit log | Mission Control" };

// The browser reads ?start_date&end_date from the URL (a council session links here), so it sits behind Suspense.
export default function AuditPage() {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Loading…</p>}>
      <AuditBrowser />
    </Suspense>
  );
}
