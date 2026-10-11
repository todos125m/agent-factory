"""baseline: the schema as of PR1

Revision ID: 0001_baseline
Revises: 
Create Date: 2026-10-11 22:05:50.754078

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001_baseline'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """The schema as of PR1. Idempotent: a database the app built before migrations existed (create_all at startup)
    keeps its tables and rows, and only what it lacks is created; an empty database gets everything."""
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if 'agents' not in existing:
        op.create_table('agents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('model_role', sa.String(length=50), nullable=False),
        sa.Column('capabilities', sa.JSON(), nullable=False),
        sa.Column('tools', sa.JSON(), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=False),
        sa.Column('skills', sa.JSON(), nullable=False),
        sa.Column('instructions', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
        )
    if 'benchmark_runs' not in existing:
        op.create_table('benchmark_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('goal', sa.Text(), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('effort', sa.String(length=20), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('schema_valid', sa.Boolean(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('rating', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'settings_layers' not in existing:
        op.create_table('settings_layers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scope', sa.Enum('GLOBAL', 'WORKSPACE', 'PROJECT', 'TASK', name='settingsscope'), nullable=False),
        sa.Column('scope_id', sa.Integer(), nullable=False),
        sa.Column('values', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scope', 'scope_id')
        )
    if 'skills' not in existing:
        op.create_table('skills',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('summary', sa.String(length=300), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
        )
    if 'users' not in existing:
        op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
        )
    if 'workspaces' not in existing:
        op.create_table('workspaces',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'projects' not in existing:
        op.create_table('projects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('goal', sa.Text(), nullable=False),
        sa.Column('mode', sa.Enum('AUTOMATIC', 'MANUAL_LEARNING', name='mode'), nullable=False),
        sa.Column('stage', sa.Enum('IDEA', 'DISCOVERY', 'VALIDATION', 'PRODUCT_DEFINITION', 'BUILDING', 'REVIEW', 'LAUNCH', 'MEASUREMENT', 'ITERATION', name='projectstage'), nullable=False),
        sa.Column('paused', sa.Boolean(), nullable=False),
        sa.Column('budget', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'user_preferences' not in existing:
        op.create_table('user_preferences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('ui_locale', sa.String(length=8), nullable=True),
        sa.Column('audience_level', sa.String(length=20), nullable=True),
        sa.Column('code_visibility', sa.String(length=10), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
        )
    if 'chat_messages' not in existing:
        op.create_table('chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('suggested_action', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
    if 'tasks' not in existing:
        op.create_table('tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('owner', sa.String(length=100), nullable=True),
        sa.Column('input', sa.JSON(), nullable=False),
        sa.Column('output', sa.JSON(), nullable=True),
        sa.Column('status', sa.Enum('CREATED', 'READY', 'RUNNING', 'WAITING', 'COMPLETED', 'REVIEWED', 'FAILED', 'ESCALATED', 'CANCELLED', name='taskstatus'), nullable=False),
        sa.Column('budget', sa.Float(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('risk', sa.Enum('LOW', 'MEDIUM', 'HIGH', name='risklevel'), nullable=False),
        sa.Column('mode', sa.Enum('AUTOMATIC', 'MANUAL_LEARNING', name='mode'), nullable=True),
        sa.Column('retries', sa.Integer(), nullable=False),
        sa.Column('max_retries', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
    if 'budget_holds' not in existing:
        op.create_table('budget_holds',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('usd', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
        )
    op.create_index('ix_budget_holds_project_id', 'budget_holds', ['project_id'], unique=False, if_not_exists=True)
    op.create_index('ix_budget_holds_task_id', 'budget_holds', ['task_id'], unique=False, if_not_exists=True)
    if 'feedback' not in existing:
        op.create_table('feedback',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('agent', sa.String(length=100), nullable=False),
        sa.Column('rating', sa.String(length=10), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
        )
    if 'learning_traces' not in existing:
        op.create_table('learning_traces',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('concept', sa.String(length=300), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=False),
        sa.Column('example', sa.Text(), nullable=True),
        sa.Column('related_decision', sa.Text(), nullable=True),
        sa.Column('user_question', sa.Text(), nullable=True),
        sa.Column('mastery_signal', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
        )
    if 'memory_items' not in existing:
        op.create_table('memory_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('category', sa.Enum('BRIEF', 'DECISIONS', 'RESEARCH', 'CUSTOMER', 'STRATEGY', 'PRODUCT', 'TECHNICAL', 'EXPERIMENTS', 'LEARNING', name='memorycategory'), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
        )
    if 'model_calls' not in existing:
        op.create_table('model_calls',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('agent', sa.String(length=100), nullable=True),
        sa.Column('role', sa.String(length=50), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
        sa.Column('cache_write_tokens', sa.Integer(), nullable=False),
        sa.Column('web_searches', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
        )
    op.create_index('ix_model_calls_project_id', 'model_calls', ['project_id'], unique=False, if_not_exists=True)
    op.create_index('ix_model_calls_task_id', 'model_calls', ['task_id'], unique=False, if_not_exists=True)
    if 'run_events' not in existing:
        op.create_table('run_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('type', sa.String(length=100), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
    if 'task_dependencies' not in existing:
        op.create_table('task_dependencies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('depends_on_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['depends_on_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', 'depends_on_id')
        )


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('task_dependencies')
    op.drop_table('run_events')
    with op.batch_alter_table('model_calls', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_model_calls_task_id'))
        batch_op.drop_index(batch_op.f('ix_model_calls_project_id'))

    op.drop_table('model_calls')
    op.drop_table('memory_items')
    op.drop_table('learning_traces')
    op.drop_table('feedback')
    with op.batch_alter_table('budget_holds', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_budget_holds_task_id'))
        batch_op.drop_index(batch_op.f('ix_budget_holds_project_id'))

    op.drop_table('budget_holds')
    op.drop_table('tasks')
    op.drop_table('chat_messages')
    op.drop_table('user_preferences')
    op.drop_table('projects')
    op.drop_table('workspaces')
    op.drop_table('users')
    op.drop_table('skills')
    op.drop_table('settings_layers')
    op.drop_table('benchmark_runs')
    op.drop_table('agents')
    # ### end Alembic commands ###
