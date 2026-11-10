"""
The roster of executive agents that sit under the Executive Constitution.

Adding an agent means: add an entry here, write its agent module (build its system
prompt with base_constitution.build_system_prompt), and set its key. The governance
layer (audit log, kill switch, cross-validation) then works for it without further
changes. The CAO, the COO, the CTO and the CMO are built (Phases 1 to 4 of the AI Executive Team).

An agent is "available" when its answer function exists. A planned agent is listed
with ``answer_path=None`` so that the kill switch can already be armed for it and so
that tests can exercise cross-validation against a stand-in before it exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    title: str
    # Environment variable holding the key that gates this agent's endpoints.
    key_env: str
    # Dotted path "module:function" of the async answer function, or None while planned.
    answer_path: str | None

    @property
    def available(self) -> bool:
        return self.answer_path is not None


AGENTS: dict[str, AgentSpec] = {
    "cao": AgentSpec("cao", "Chief Aquaculture Officer", "INTERNAL_CAO_KEY", "app.agents.cao_agent:get_cao_answer"),
    # Phase 2 of the AI Executive Team. Its own key (INTERNAL_COO_KEY) gates POST /api/coo.
    "coo": AgentSpec("coo", "Chief Operating Officer", "INTERNAL_COO_KEY", "app.agents.coo_agent:get_coo_answer"),
    # Phase 3 of the AI Executive Team. Its own key (INTERNAL_CTO_KEY) gates POST /api/cto.
    "cto": AgentSpec("cto", "Chief Technology Officer", "INTERNAL_CTO_KEY", "app.agents.cto_agent:get_cto_answer"),
    # Phase 4 of the AI Executive Team. Its own key (INTERNAL_CMO_KEY) gates POST /api/cmo.
    "cmo": AgentSpec("cmo", "Chief Marketing & Media Officer", "INTERNAL_CMO_KEY", "app.agents.cmo_agent:get_cmo_answer"),
}


def is_known(agent_id: str) -> bool:
    return agent_id in AGENTS


def spec(agent_id: str) -> AgentSpec:
    try:
        return AGENTS[agent_id]
    except KeyError:
        raise KeyError(f"unknown agent: {agent_id!r}") from None


def expected_key(agent_id: str) -> str:
    """The configured key for this agent ('' when unset, which keeps its endpoints closed)."""
    return os.getenv(spec(agent_id).key_env, "")


def load_answer_function(agent_id: str):
    """Import and return the agent's async answer function; None for a planned agent."""
    import importlib

    path = spec(agent_id).answer_path
    if path is None:
        return None
    module_name, function_name = path.split(":")
    return getattr(importlib.import_module(module_name), function_name)
