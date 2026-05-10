"""add_jobs_tables

Revision ID: c3d4e5f6a7b8
Revises: b20bbe62572d
Create Date: 2026-05-09 01:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b20bbe62572d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create job and trackevent tables."""
    op.create_table(
        'job',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), primary_key=True, nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='queued'),
        sa.Column('source_config', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('targets_config', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('recurring', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('interval_minutes', sa.Integer(), nullable=True),
        sa.Column('run_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('tracks_fetched', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tracks_ok', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tracks_err', sa.Integer(), nullable=False, server_default='0'),
    )

    op.create_table(
        'trackevent',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), primary_key=True, nullable=False),
        sa.Column('job_id', sqlmodel.sql.sqltypes.AutoString(), sa.ForeignKey('job.id'), nullable=False, index=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('artist', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('target_service', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('success', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )

    op.create_index('ix_trackevent_job_id', 'trackevent', ['job_id'])


def downgrade() -> None:
    """Drop job and trackevent tables."""
    op.drop_index('ix_trackevent_job_id', table_name='trackevent')
    op.drop_table('trackevent')
    op.drop_table('job')
