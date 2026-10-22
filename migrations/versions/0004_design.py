"""design: blueprint revisions, a budget per build, model calls and holds counted per build (issue #2 PR3)

Revision ID: 0004_design
Revises: 0003_factory_intake
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_design"
down_revision: Union[str, Sequence[str], None] = "0003_factory_intake"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    """Idempotent, like 0001–0003: a database create_all built from these models gets only what it lacks."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "blueprint_revisions" not in set(inspector.get_table_names()):
        op.create_table(
            "blueprint_revisions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("brief_version", sa.Integer(), nullable=False),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("blueprint", sa.JSON(), nullable=False),
            sa.Column("branches", sa.JSON(), nullable=False),
            sa.Column("issues", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("reason", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["session_id"], ["factory_sessions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("session_id", "version"),
        )
    op.create_index("ix_blueprint_revisions_session_id", "blueprint_revisions", ["session_id"], unique=False,
                    if_not_exists=True)
    if "budget_usd" not in _columns(inspector, "factory_sessions"):
        with op.batch_alter_table("factory_sessions") as batch:
            batch.add_column(sa.Column("budget_usd", sa.Float(), nullable=False, server_default="1.0"))
    if "factory_session_id" not in _columns(inspector, "budget_holds"):
        with op.batch_alter_table("budget_holds") as batch:
            batch.add_column(sa.Column("factory_session_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_budget_holds_factory_session_id", "factory_sessions", ["factory_session_id"],
                                     ["id"], ondelete="CASCADE")
    op.create_index("ix_budget_holds_factory_session_id", "budget_holds", ["factory_session_id"], unique=False,
                    if_not_exists=True)
    if "factory_session_id" not in _columns(inspector, "model_calls"):
        with op.batch_alter_table("model_calls") as batch:
            batch.add_column(sa.Column("factory_session_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_model_calls_factory_session_id", "factory_sessions", ["factory_session_id"],
                                     ["id"], ondelete="SET NULL")
    op.create_index("ix_model_calls_factory_session_id", "model_calls", ["factory_session_id"], unique=False,
                    if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_model_calls_factory_session_id", table_name="model_calls")
    with op.batch_alter_table("model_calls") as batch:
        batch.drop_constraint("fk_model_calls_factory_session_id", type_="foreignkey")
        batch.drop_column("factory_session_id")
    op.drop_index("ix_budget_holds_factory_session_id", table_name="budget_holds")
    with op.batch_alter_table("budget_holds") as batch:
        batch.drop_constraint("fk_budget_holds_factory_session_id", type_="foreignkey")
        batch.drop_column("factory_session_id")
    with op.batch_alter_table("factory_sessions") as batch:
        batch.drop_column("budget_usd")
    op.drop_index("ix_blueprint_revisions_session_id", table_name="blueprint_revisions")
    op.drop_table("blueprint_revisions")
