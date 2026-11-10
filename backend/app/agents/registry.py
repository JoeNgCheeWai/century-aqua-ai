"""
The roster of executive agents that sit under the Executive Constitution.

Adding the AI_COO later means: add an entry here, write its agent module (build
its system prompt with base_constitution.build_system_prompt), and set its key.
The governance layer (audit log, kill switch, cross-validation) then works for it
without further changes.

An agent is "available" when its answer function exists. Planned agents (the COO)
are listed so that the kill switch can already be armed and so that tests can
exercise cross-validation against a stand-in before the real agent exists.
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
    # Planned: the COO is layered on top of the constitution next. No answer function yet.
    "coo": AgentSpec("coo", "Chief Operating Officer", "INTERNAL_COO_KEY", None),
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
