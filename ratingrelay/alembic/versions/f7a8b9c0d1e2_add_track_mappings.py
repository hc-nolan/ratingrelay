"""add_track_mappings

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-05-09 03:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'trackmapping',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), primary_key=True, nullable=False),
        sa.Column('source_normalized_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('source_artist', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('source_title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('mapped_artist', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('mapped_title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('recording_mbid', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('target_service', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='all'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index(
        'ix_trackmapping_lookup',
        'trackmapping',
        ['source_normalized_key', 'target_service'],
    )


def downgrade() -> None:
    op.drop_index('ix_trackmapping_lookup', table_name='trackmapping')
    op.drop_table('trackmapping')
