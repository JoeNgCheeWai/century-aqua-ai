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

export default function InternalHomePage() {
  return (
    <ComingSoon
      title="Internal Workspace"
      phase="Phase 2"
      description="Your team's workspace for working with the AI agents and with each other. The layout and access control are live; the chat and collaboration features are coming next."
      planned={[
        "Chat with an agent of your choice: Dr. Aqua (unlimited for staff) or the AI_CAO executive agent",
        "A warning banner when the AI_CAO is selected, since it draws on proprietary knowledge",
        "Every internal chat saved separately from subscriber chats",
      ]}
    >
      <Suspense fallback={null}>
        <Welcome />
      </Suspense>
    </ComingSoon>
  );
}
