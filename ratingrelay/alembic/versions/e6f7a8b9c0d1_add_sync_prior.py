"""add_sync_prior

Revision ID: e6f7a8b9c0d1
Revises: c4d5e6f7a8b9
Create Date: 2026-11-15 00:00:00.000000

``syncrecord.prior`` is what a target held before this tool first wrote there,
in the same target-owned form as ``target_key``, so undo puts it back instead of
clearing it (a Plex rating the user had, a hate a love replaced). Existing rows
get NULL and undo the way they always did.

Autogenerate also proposed dropping ``ix_syncrecord_lookup`` and
``ix_trackmapping_lookup``. Those indexes are created by migrations and not
declared on the models, so the drops were removed: they are not part of this
change.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('syncrecord') as batch:
        batch.add_column(sa.Column('prior', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('syncrecord') as batch:
        batch.drop_column('prior')
