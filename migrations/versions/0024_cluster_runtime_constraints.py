"""Enforce cluster scheduling and guardian accounting invariants."""
from __future__ import annotations

import os
import re

from alembic import op


revision = "0024_cluster_constraints"
down_revision = "0023_cluster_deployments"
branch_labels = None
depends_on = None


def _schema() -> str:
    value = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise RuntimeError("Invalid PostgreSQL schema")
    return value


def upgrade() -> None:
    schema = _schema()
    op.create_check_constraint(
        "ck_fleet_worker_policy_limits",
        "fleet_worker_policies",
        "cpu_limit_millis >= 0 AND memory_limit_bytes >= 0 AND "
        "gpu_limit_slots >= 0 AND resource_version >= 1",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_fleet_borrow_grant_budget",
        "fleet_borrow_grants",
        "budget_limit_microunits >= 0 AND budget_reserved_microunits >= 0 AND "
        "budget_spent_microunits >= 0 AND "
        "budget_reserved_microunits + budget_spent_microunits "
        "<= budget_limit_microunits AND max_priority BETWEEN 0 AND 100 AND "
        "resource_version >= 1",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_fleet_guardian_action_accounting",
        "fleet_guardians",
        "actions_used >= 0 AND actions_used <= max_actions AND "
        "consecutive_failures >= 0 AND resource_version >= 1",
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_constraint(
        "ck_fleet_guardian_action_accounting",
        "fleet_guardians",
        type_="check",
        schema=schema,
    )
    op.drop_constraint(
        "ck_fleet_borrow_grant_budget",
        "fleet_borrow_grants",
        type_="check",
        schema=schema,
    )
    op.drop_constraint(
        "ck_fleet_worker_policy_limits",
        "fleet_worker_policies",
        type_="check",
        schema=schema,
    )
