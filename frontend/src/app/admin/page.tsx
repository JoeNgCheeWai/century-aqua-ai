import { Suspense } from "react";
import { currentUser } from "@clerk/nextjs/server";
import ComingSoon from "@/components/staff/ComingSoon";

async function Welcome() {
  const user = await currentUser();
  const name = user?.firstName || user?.primaryEmailAddress?.emailAddress || "colleague";
  return (
    <p className="mt-6 rounded-xl border border-[#00C9A7]/30 bg-[#00C9A7]/10 px-4 py-3 text-sm text-[#00C9A7]">
      Access check passed: signed in as {name}, role <strong>internal</strong>.
    </p>
  );
}

export default function AdminOverviewPage() {
  return (
    <ComingSoon
      title="Overview"
      phase="Phase 3"
      description="One-screen view of how the platform is doing today. The layout and access control are live; the live numbers need query logging, which comes first in Phase 3."
      planned={[
        "Today's stats: total queries, unique users, new sign-ups",
        "Status lights for the backend, database, Pinecone and OpenAI",
        "Recent alerts: errors, rate-limit hits and cost spikes from the last 24 hours",
        "Quick actions: start a knowledge-base ingestion, review logs, manage users",
      ]}
    >
      <Suspense fallback={null}>
        <Welcome />
      </Suspense>
    </ComingSoon>
  );
}
