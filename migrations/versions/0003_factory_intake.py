"""factory intake: sessions, brief revisions, approvals

Revision ID: 0003_factory_intake
Revises: 0002_auth
Create Date: 2026-10-11 22:37:02.728843

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003_factory_intake'
down_revision: Union[str, Sequence[str], None] = '0002_auth'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent, like 0001/0002: a database create_all built from these models gets only what it lacks."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if 'factory_sessions' not in existing:
        op.create_table('factory_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('stage', sa.String(length=40), nullable=False),
        sa.Column('stage_before_pause', sa.String(length=40), nullable=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('locale', sa.String(length=8), nullable=False),
        sa.Column('interaction_policy', sa.String(length=30), nullable=False),
        sa.Column('quality_target', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
    op.create_index('ix_factory_sessions_owner_id', 'factory_sessions', ['owner_id'], unique=False, if_not_exists=True)
    if 'approvals' not in existing:
        op.create_table('approvals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('stage', sa.String(length=40), nullable=False),
        sa.Column('resource', sa.String(length=30), nullable=False),
        sa.Column('resource_version', sa.Integer(), nullable=False),
        sa.Column('scope', sa.JSON(), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('invalidated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('invalidated_reason', sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['session_id'], ['factory_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
    op.create_index('ix_approvals_session_id', 'approvals', ['session_id'], unique=False, if_not_exists=True)
    if 'brief_revisions' not in existing:
        op.create_table('brief_revisions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('fields', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['session_id'], ['factory_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'version')
        )
    op.create_index('ix_brief_revisions_session_id', 'brief_revisions', ['session_id'], unique=False, if_not_exists=True)
    if 'factory_requests' not in existing:
        op.create_table('factory_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['session_id'], ['factory_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_id', 'request_id')
        )
    if "factory_session_id" not in {c["name"] for c in inspector.get_columns("run_events")}:
        # An event is a project's or a factory session's (app/models.py::RunEvent).
        with op.batch_alter_table("run_events") as batch:
            batch.add_column(sa.Column("factory_session_id", sa.Integer(), nullable=True))
            batch.alter_column("project_id", existing_type=sa.INTEGER(), nullable=True)
            batch.create_foreign_key("fk_run_events_factory_session_id", "factory_sessions", ["factory_session_id"],
                                     ["id"], ondelete="CASCADE")
    op.create_index("ix_run_events_factory_session_id", "run_events", ["factory_session_id"], unique=False,
                    if_not_exists=True)


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('run_events', schema=None) as batch_op:
        batch_op.drop_constraint('fk_run_events_factory_session_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_run_events_factory_session_id'))
        batch_op.alter_column('project_id',
               existing_type=sa.INTEGER(),
               nullable=False)
        batch_op.drop_column('factory_session_id')

    op.drop_table('factory_requests')
    with op.batch_alter_table('brief_revisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_brief_revisions_session_id'))

    op.drop_table('brief_revisions')
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_approvals_session_id'))

    op.drop_table('approvals')
    with op.batch_alter_table('factory_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_factory_sessions_owner_id'))

    op.drop_table('factory_sessions')
    # ### end Alembic commands ###
