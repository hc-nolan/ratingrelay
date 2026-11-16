"""add_relay_identity

Revision ID: c4d5e6f7a8b9
Revises: b1c2d3e4f5a6
Create Date: 2026-11-15 00:00:00.000000

Two relays on the same source → target pair (Plex loves and Plex hates, both to
ListenBrainz) shared one set of sync records, so each run treated the other
relay's writes as withdrawn and undid them. ``syncrecord.relay`` says which relay
wrote a row. Existing rows get ``""``: nothing in the database says which relay
wrote them, so the first relay to run on the pair claims them (see
``SqlSyncStore.load``) rather than this migration guessing.

``job.allow_mass_undo`` is the one-run override for the guard that refuses to
undo most of a target's records at once.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('syncrecord') as batch:
        batch.add_column(
            sa.Column('relay', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='')
        )
        batch.drop_index('ix_syncrecord_lookup')
        batch.create_index(
            'ix_syncrecord_lookup',
            ['source_service', 'target_service', 'relay', 'normalized_key'],
        )
    op.add_column(
        'job',
        sa.Column('allow_mass_undo', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    with op.batch_alter_table('job') as batch:
        batch.drop_column('allow_mass_undo')
    with op.batch_alter_table('syncrecord') as batch:
        batch.drop_index('ix_syncrecord_lookup')
        batch.create_index(
            'ix_syncrecord_lookup',
            ['source_service', 'target_service', 'normalized_key'],
        )
        batch.drop_column('relay')
