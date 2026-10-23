"""credentials: encrypted provider keys (issue #2 PR6b)

Revision ID: 0008_credentials
Revises: 0007_workflow
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_credentials"
down_revision: Union[str, Sequence[str], None] = "0007_workflow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent, like 0001–0007: only what is missing is created."""
    if "credentials" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "credentials",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("provider", sa.String(length=20), nullable=False),
            sa.Column("ciphertext", sa.Text(), nullable=False),
            sa.Column("last4", sa.String(length=4), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column("test_result", sa.String(length=20), nullable=True),
            sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", "request_id"),
        )
    op.create_index("ix_credentials_owner_id", "credentials", ["owner_id"], unique=False, if_not_exists=True)


def downgrade() -> None:
    op.drop_table("credentials")
