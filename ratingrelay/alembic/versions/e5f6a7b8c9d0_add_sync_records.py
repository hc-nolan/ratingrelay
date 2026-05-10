"""add_sync_records

Revision ID: e5f6a7b8c9d0
Revises: c3d4e5f6a7b8
Create Date: 2026-05-09 02:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'syncrecord',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), primary_key=True, nullable=False),
        sa.Column('source_service', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('target_service', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('normalized_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('recording_mbid', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), nullable=False),
    )
    op.create_index(
        'ix_syncrecord_lookup',
        'syncrecord',
        ['source_service', 'target_service', 'normalized_key'],
    )
    op.add_column('job', sa.Column('tracks_skipped', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_index('ix_syncrecord_lookup', table_name='syncrecord')
    op.drop_table('syncrecord')
    op.drop_column('job', 'tracks_skipped')
