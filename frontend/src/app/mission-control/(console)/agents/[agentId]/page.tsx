import { Suspense } from "react";
import { notFound } from "next/navigation";
import { AGENT_KEYS } from "@/lib/mc-types";
import AgentView, { type Target } from "./AgentView";

export const metadata = { title: "Ask an agent | Mission Control" };

// The four executives, plus "council" (the same page convenes a council session instead of asking one agent).
const TARGETS = [...AGENT_KEYS, "council"];

export async function generateStaticParams() {
  return TARGETS.map((agentId) => ({ agentId }));
}

// The id is read from the URL, which Cache Components treats as request-time data, so it sits behind Suspense
// (the same pattern as the council session page).
async function Agent({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = await params;
  if (!TARGETS.includes(agentId)) notFound();
  return <AgentView target={agentId as Target} />;
}

export default function AgentPage({ params }: { params: Promise<{ agentId: string }> }) {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Loading…</p>}>
      <Agent params={params} />
    </Suspense>
  );
}
