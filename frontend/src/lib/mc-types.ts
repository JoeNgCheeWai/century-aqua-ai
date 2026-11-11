/** Shapes of the backend responses the Mission Control pages read (snake_case, exactly as the FastAPI backend sends them). */

export type AgentKey = "cao" | "coo" | "cto" | "cmo";
export type HumanAction = "Pending" | "Approved" | "Modified" | "Rejected";
export type CouncilStatus = "RUNNING" | "RESOLVED" | "ESCALATED_TO_CEO" | "NEEDS_CEO_INPUT" | "FAILED";

export interface Paged<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** What approving would do (Phase 6.6): open a pull request from this branch. It is never merged by the system. */
export interface ExecutionPlan {
  branch_name: string;
  files_to_update: { path: string; content: string; commit_message: string }[];
  pr_title: string;
  pr_body: string;
}

/** What happened after approval (Phase 6.6), stored on the authorization request by the backend. */
export interface ExecutionOutcome {
  status: "running" | "succeeded" | "failed" | "refused" | "unconfigured" | "already_claimed";
  steps?: { tool: string; ok: boolean; message: string }[];
  pr_url?: string | null;
  branch?: string | null;
  error?: string | null;
}

/** What is it for, what problem does it solve, what could go wrong. The risk RATING is the badge, computed by the backend. */
export interface PlainSummary {
  purpose: string;
  problem: string;
  risk: string;
}

/** The CTO's request has proposed_code_diff / proposed_commands; the CMO's has proposed_content / deployment_commands. */
export interface AuthorizationRequest {
  action_summary: string;
  /** Three plain-language answers for a non-technical decision-maker (Phase 6.5). Missing on older rows. */
  executive_summary?: PlainSummary | null;
  risk_level: "low" | "medium" | "high" | "critical";
  impact: string;
  proposed_code_diff?: string | null;
  proposed_commands?: string[];
  proposed_content?: string | null;
  deployment_commands?: string[];
  rollback_plan?: string | null;
  claims_to_verify?: string[];
  /** Phase 6.6: placeholder images (placehold.co) from the CMO's mock_generate_image tool. */
  mockup_urls?: string[];
  execution_plan?: ExecutionPlan | null;
  /** Written by the backend after approval when there was a plan. Absent before. */
  execution?: ExecutionOutcome | null;
  status: string;
  requires_human_approval: boolean;
  executed: boolean;
}

export interface Scenario {
  description: string;
  timeline: string;
  resource_requirements: string;
  confidence_percentage: number;
  key_assumptions: string[];
  risks: string[];
}
export interface Scenarios {
  optimistic: Scenario;
  baseline: Scenario;
  pessimistic: Scenario;
}

export interface AuditEntry {
  id: number;
  agent_id: string;
  user_id: string;
  query: string;
  response: string;
  constitutional_score: number;
  needs_review: boolean;
  sources_cited: string[];
  reasoning_chain: string[];
  scenarios: Scenarios | null;
  pre_consultation_requests: { coo_query?: string | null; cto_query?: string | null } | null;
  authorization_request: AuthorizationRequest | null;
  human_action: HumanAction;
  human_action_at: string | null;
  human_action_by: string | null;
  timestamp: string | null;
}

/** What POST /api/{cao|coo|cto|cmo} returns. */
export interface AgentAnswer {
  response: string;
  /** The short conversational answer shown first (Phase 6.5). Null when the model gave none or it was vetted out. */
  chat_reply?: string | null;
  /** The id of the model that wrote this answer. */
  model?: string | null;
  reasoning_chain: string[];
  scenarios: Scenarios | null;
  authorization_request?: AuthorizationRequest | null;
  constitutional_score: number | null;
  needs_review: boolean | null;
  audit_id: number | null;
  reset_epoch: number | null;
  sources: string[];
}

export interface CouncilSummary {
  session_id: string;
  query: string;
  status: CouncilStatus;
  participating_agents: string[];
  started_at: string | null;
  completed_at: string | null;
  model_calls: number;
  total_tokens: number | null;
  option_count: number;
  human_action: HumanAction;
  escalation_reason: string | null;
}

export interface CouncilOption {
  option_id: string;
  title: string;
  summary: string;
  assumptions: string[];
  supported_by: string[];
  dissent_from: string[];
  unresolved_objection: string;
  most_likely_failure_mode: string;
  proposed_action?: { action_summary: string; status: string; executed: boolean; requires_human_approval: boolean } | null;
}

export interface CouncilTurn {
  round: number;
  agent_id: string;
  position: string;
  hard_constraints: { constraint: string; source: string }[] | null;
  confidence: number | null;
  sources_cited: string[];
  tokens_used: number | null;
}

export interface CouncilConflict {
  topic: string;
  agents: string[];
  positions: string;
  resolved: boolean;
}

export interface CouncilVeto {
  agent_id: string;
  constraint: string;
  source: string;
  overrides: string[];
}

/** GET /api/council/{id}. While RUNNING only session_id, status and started_at are present. */
export interface CouncilSession {
  session_id: string;
  status: CouncilStatus;
  started_at: string | null;
  query?: string;
  participating_agents?: string[];
  selection_rationale?: string;
  options?: CouncilOption[] | null;
  conflict_matrix?: CouncilConflict[];
  hard_constraint_vetoes?: CouncilVeto[];
  coo_moderation_notes?: string | null;
  red_team_findings?: { option_id: string; most_likely_failure_mode: string }[] | null;
  dissent_record?: Record<string, unknown>[];
  ceo_questions?: string[];
  open_questions?: string[];
  agent_flagged_questions?: string[];
  escalation_reason?: string | null;
  model_calls?: number;
  total_tokens?: number | null;
  completed_at?: string | null;
  turns?: CouncilTurn[];
  executed?: boolean;
  requires_human_approval?: boolean;
}

export interface Overview {
  backend_reachable: boolean;
  agents: {
    agent_id: AgentKey;
    available: boolean;
    answers_on_record: number | null;
    last_activity: string | null;
    last_query: string | null;
    last_score: number | null;
  }[];
  pending_authorizations: number | null;
  recent_sessions: Paged<CouncilSummary> | null;
  errors: { audit: number | null | undefined; authorizations: number | null; sessions: number | null };
}

/**
 * Model tiers an executive can be asked with (Phase 6.5). The costs are ROUGH ESTIMATES for one typical executive answer:
 * about 7,000 input tokens (the agent's system prompt alone is about 7,500) and about 3,500 output tokens (an assumption,
 * which includes any thinking tokens), at the published per-million-token prices: Opus $4 in / $20 out, Sonnet $2 / $10,
 * Haiku $0.10 / $0.50. A long answer, a long conversation or a large knowledge-base excerpt costs more. Re-check these
 * against the pricing page when prices change; the real cost is on the Anthropic usage page, not here.
 */
export type ModelTier = "haiku" | "sonnet" | "opus";
export const MODEL_TIERS: { id: ModelTier; label: string; icon: string; hint: string; cost: string }[] = [
  { id: "opus", label: "Opus", icon: "🧠", hint: "High-stakes, strategic analysis", cost: "~$0.10" },
  { id: "sonnet", label: "Sonnet", icon: "⚡", hint: "Everyday work, balanced", cost: "~$0.05" },
  { id: "haiku", label: "Haiku", icon: "🚀", hint: "Quick replies, simple questions", cost: "~$0.003" },
];
export const DEFAULT_TIER: ModelTier = "sonnet";

export const AGENT_INFO: Record<AgentKey, { name: string; role: string }> = {
  cao: { name: "AI_CAO", role: "Chief Aquaculture Officer" },
  coo: { name: "AI_COO", role: "Chief Operating Officer" },
  cto: { name: "AI_CTO", role: "Chief Technology Officer" },
  cmo: { name: "AI_CMO", role: "Chief Marketing & Media Officer" },
};
export const AGENT_KEYS = Object.keys(AGENT_INFO) as AgentKey[];
export const TERMINAL: CouncilStatus[] = ["RESOLVED", "ESCALATED_TO_CEO", "NEEDS_CEO_INPUT", "FAILED"];
