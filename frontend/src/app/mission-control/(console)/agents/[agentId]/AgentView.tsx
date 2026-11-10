"use client";

import Link from "next/link";
import AgentChat from "@/components/mc/AgentChat";
import CouncilChat from "@/components/mc/CouncilChat";
import { PageTitle } from "@/components/mc/ui";
import { AGENT_INFO, AGENT_KEYS, type AgentKey } from "@/lib/mc-types";

export type Target = AgentKey | "council";

function Tabs({ target }: { target: Target }) {
  return (
    <nav className="mb-5 flex flex-wrap gap-2" aria-label="Choose who to ask">
      {[...AGENT_KEYS, "council" as const].map((key) => (
        <Link
          key={key}
          href={`/mission-control/agents/${key}`}
          aria-current={key === target ? "page" : undefined}
          className={`rounded-lg border px-3 py-1.5 text-sm transition ${
            key === target ? "border-cyan-400/60 bg-cyan-400/15 text-cyan-100" : "border-slate-700 text-slate-400 hover:border-cyan-400/30 hover:text-white"
          }`}
        >
          {key === "council" ? "Council" : AGENT_INFO[key].name}
        </Link>
      ))}
    </nav>
  );
}

export default function AgentView({ target }: { target: Target }) {
  return (
    <>
      <PageTitle
        title={target === "council" ? "Council" : AGENT_INFO[target].name}
        subtitle={target === "council" ? "Convene the executive council on a decision." : AGENT_INFO[target].role}
      />
      <Tabs target={target} />
      {/* key: switching agent starts a fresh conversation */}
      {target === "council" ? <CouncilChat key="council" /> : <AgentChat key={target} agent={target} />}
    </>
  );
}
