"""build: agent versions and their evaluations (issue #2 PR5)

Revision ID: 0006_build
Revises: 0005_runtime
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_build"
down_revision: Union[str, Sequence[str], None] = "0005_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent, like 0001–0005: only what is missing is created."""
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "agent_versions" not in existing:
        op.create_table(
            "agent_versions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("agent_name", sa.String(length=100), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("blueprint_version", sa.Integer(), nullable=False),
            sa.Column("spec", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(length=20), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["session_id"], ["factory_sessions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("agent_name", "version"),
        )
    op.create_index("ix_agent_versions_session_id", "agent_versions", ["session_id"], unique=False, if_not_exists=True)
    op.create_index("ix_agent_versions_agent_name", "agent_versions", ["agent_name"], unique=False, if_not_exists=True)
    if "evaluations" not in existing:
        op.create_table(
            "evaluations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("agent_version_id", sa.Integer(), nullable=False),
            sa.Column("round", sa.Integer(), nullable=False),
            sa.Column("verdict", sa.String(length=20), nullable=False),
            sa.Column("cases", sa.JSON(), nullable=False),
            sa.Column("cost_usd", sa.Float(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["session_id"], ["factory_sessions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    op.create_index("ix_evaluations_session_id", "evaluations", ["session_id"], unique=False, if_not_exists=True)
    op.create_index("ix_evaluations_agent_version_id", "evaluations", ["agent_version_id"], unique=False,
                    if_not_exists=True)


def downgrade() -> None:
    op.drop_table("evaluations")
    op.drop_table("agent_versions")
