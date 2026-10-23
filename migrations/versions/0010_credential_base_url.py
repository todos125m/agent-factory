"""credentials.base_url: a connection to any OpenAI-compatible provider (issue #2 follow-up)

Revision ID: 0010_credential_base_url
Revises: 0009_api_keys
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_credential_base_url"
down_revision: Union[str, Sequence[str], None] = "0009_api_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent, like 0001-0009: only what is missing is added."""
    inspector = sa.inspect(op.get_bind())
    if "credentials" in set(inspector.get_table_names()) and "base_url" not in {c["name"] for c in inspector.get_columns("credentials")}:
        op.add_column("credentials", sa.Column("base_url", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("credentials", "base_url")
