import { notFound } from "next/navigation";
import { AGENT_KEYS } from "@/lib/mc-types";
import AgentView, { type Target } from "./AgentView";

export const metadata = { title: "Ask an agent | Mission Control" };

// The four executives, plus "council" (the same page convenes a council session instead of asking one agent).
const TARGETS = [...AGENT_KEYS, "council"];

export async function generateStaticParams() {
  return TARGETS.map((agentId) => ({ agentId }));
}

export default async function AgentPage({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = await params;
  if (!TARGETS.includes(agentId)) notFound();
  return <AgentView target={agentId as Target} />;
}
