"""
Model tiers for the executive agents (Phase 6.5).

A caller may pick a TIER ("haiku", "sonnet" or "opus"), never a model id. The tier is mapped to a model id HERE, from an
allow-list, so a request can never name an arbitrary (and possibly far more expensive) model. When no tier is sent, each
agent keeps its own model (CAO_MODEL, COO_MODEL, CTO_MODEL, CMO_MODEL), so existing callers behave exactly as before.

The ids can be changed without a code change through HAIKU_MODEL, SONNET_MODEL and OPUS_MODEL. They are read when a call
is made, not at import time.

What this does NOT do: it does not decide which tier a question deserves, and it does not guarantee that a cheaper tier
follows the output format or the constitution as well as a stronger one does. The constitutional score and the
needs_review flag still apply to every answer, whichever tier wrote it.
"""

from __future__ import annotations

import os
from typing import Literal, get_args

ModelTier = Literal["haiku", "sonnet", "opus"]
TIERS: tuple[str, ...] = get_args(ModelTier)

# tier -> (environment variable that may override it, default model id)
_TIER_MODELS: dict[str, tuple[str, str]] = {
    "haiku": ("HAIKU_MODEL", "claude-haiku-5-5"),
    "sonnet": ("SONNET_MODEL", "claude-sonnet-5-5"),
    "opus": ("OPUS_MODEL", "claude-opus-5-5"),
}


def tier_model(tier: str) -> str:
    """The model id for a tier. Raises ValueError for anything that is not one of TIERS."""
    try:
        env_name, default = _TIER_MODELS[tier]
    except KeyError:
        raise ValueError(f"unknown model tier: {tier!r}") from None
    return os.getenv(env_name, "").strip() or default


def resolve_model(tier: str | None, default: str) -> str:
    """The agent's own model when no tier was asked for, otherwise the tier's model."""
    return default if tier is None else tier_model(tier)
