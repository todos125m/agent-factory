"""workflow: workflows and their immutable versions (issue #2 PR6)

Revision ID: 0007_workflow
Revises: 0006_build
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_workflow"
down_revision: Union[str, Sequence[str], None] = "0006_build"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent, like 0001–0006: only what is missing is created."""
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "workflows" not in existing:
        op.create_table(
            "workflows",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("layout", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", "request_id"),
        )
    op.create_index("ix_workflows_owner_id", "workflows", ["owner_id"], unique=False, if_not_exists=True)
    if "workflow_versions" not in existing:
        op.create_table(
            "workflow_versions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("workflow_id", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("nodes", sa.JSON(), nullable=False),
            sa.Column("edges", sa.JSON(), nullable=False),
            sa.Column("issues", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_by", sa.Integer(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workflow_id", "version"),
        )
    op.create_index("ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"], unique=False,
                    if_not_exists=True)


def downgrade() -> None:
    op.drop_table("workflow_versions")
    op.drop_table("workflows")
