"""agents.output_schema: an agent's own locked output shape (issue #2 follow-up)

Revision ID: 0011_agent_output_schema
Revises: 0010_credential_base_url
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_agent_output_schema"
down_revision: Union[str, Sequence[str], None] = "0010_credential_base_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent, like 0001-0010: only what is missing is added."""
    inspector = sa.inspect(op.get_bind())
    if "agents" in set(inspector.get_table_names()) and "output_schema" not in {c["name"] for c in inspector.get_columns("agents")}:
        op.add_column("agents", sa.Column("output_schema", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "output_schema")
