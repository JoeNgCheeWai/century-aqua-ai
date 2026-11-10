"""
Constitutional governance for executive agents.

    db            the one place that talks to the shared PostgreSQL database
    scoring       automatic surface check of a response against the constitution
    audit         AgentAuditLog: one row per executive-agent response
    kill_switch   atomic reset of an agent (AgentState)
    rate_limit    per-pair limit for cross-validation

The tables are created and owned by the frontend's Prisma migrations
(frontend/prisma/migrations). This package only reads and writes them.
"""
