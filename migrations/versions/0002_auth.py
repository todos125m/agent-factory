"""login: sessions, one-time login codes, the admin, workspace owners (owner decision d33)

Revision ID: 0002_auth
Revises: 0001_baseline

Existing data: every account made before login existed becomes claimable once (POST /auth/claim) by the browser that
made it; the first of them is the server's admin and owns the workspaces made so far (they had no owner).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_auth"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent, like the baseline: a database built by create_all from these models before it had a migration
    history already has all of this, and gets only what it lacks."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    user_columns = {c["name"] for c in inspector.get_columns("users")}
    workspace_columns = {c["name"] for c in inspector.get_columns("workspaces")}
    new_user_columns = [
        sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
        for name in ("is_admin", "claimable") if name not in user_columns
    ]
    if new_user_columns:
        with op.batch_alter_table("users") as batch:
            for column in new_user_columns:
                batch.add_column(column)
    if "owner_id" not in workspace_columns:
        with op.batch_alter_table("workspaces") as batch:
            batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_workspaces_owner_id_users", "users", ["owner_id"], ["id"], ondelete="CASCADE")
    if "auth_sessions" not in tables:
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash"),
        )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"], unique=False, if_not_exists=True)
    if "login_codes" not in tables:
        op.create_table(
            "login_codes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("code_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code_hash"),
        )
    op.create_index("ix_login_codes_user_id", "login_codes", ["user_id"], unique=False, if_not_exists=True)
    if not new_user_columns:
        return  # the accounts already had the login columns: they were made by this version, nothing to back-fill

    users = sa.table("users", sa.column("id", sa.Integer), sa.column("is_admin", sa.Boolean),
                     sa.column("claimable", sa.Boolean))
    workspaces = sa.table("workspaces", sa.column("owner_id", sa.Integer))
    first = bind.execute(sa.select(sa.func.min(users.c.id))).scalar()
    if first is not None:
        bind.execute(users.update().values(claimable=True))
        bind.execute(users.update().where(users.c.id == first).values(is_admin=True))
        bind.execute(workspaces.update().where(workspaces.c.owner_id.is_(None)).values(owner_id=first))


def downgrade() -> None:
    op.drop_index("ix_login_codes_user_id", table_name="login_codes")
    op.drop_table("login_codes")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_constraint("fk_workspaces_owner_id_users", type_="foreignkey")
        batch.drop_column("owner_id")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("claimable")
        batch.drop_column("is_admin")
