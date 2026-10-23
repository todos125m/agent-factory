"""api keys: scoped keys for the external API; which key started a run (issue #2 PR7)

Revision ID: 0009_api_keys
Revises: 0008_credentials
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_api_keys"
down_revision: Union[str, Sequence[str], None] = "0008_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent, like 0001–0008: only what is missing is created."""
    inspector = sa.inspect(op.get_bind())
    if "api_keys" not in set(inspector.get_table_names()):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("prefix", sa.String(length=12), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("agent_name", sa.String(length=100), nullable=False),
            sa.Column("agent_version", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("max_runs_per_day", sa.Integer(), nullable=False),
            sa.Column("budget_per_run_usd", sa.Float(), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", "request_id"),
            sa.UniqueConstraint("token_hash"),
        )
    op.create_index("ix_api_keys_owner_id", "api_keys", ["owner_id"], unique=False, if_not_exists=True)
    op.create_index("ix_api_keys_project_id", "api_keys", ["project_id"], unique=False, if_not_exists=True)
    columns = {c["name"] for c in inspector.get_columns("runs")}
    if "api_key_id" not in columns:
        with op.batch_alter_table("runs") as batch:
            batch.add_column(sa.Column("api_key_id", sa.Integer(), nullable=True))
            batch.add_column(sa.Column("api_request_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key("fk_runs_api_key_id", "api_keys", ["api_key_id"], ["id"], ondelete="SET NULL")
            batch.create_unique_constraint("uq_runs_api_key_id_api_request_id", ["api_key_id", "api_request_id"])
    op.create_index("ix_runs_api_key_id", "runs", ["api_key_id"], unique=False, if_not_exists=True)


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("uq_runs_api_key_id_api_request_id", type_="unique")
        batch.drop_constraint("fk_runs_api_key_id", type_="foreignkey")
        batch.drop_column("api_request_id")
        batch.drop_column("api_key_id")
    op.drop_table("api_keys")
