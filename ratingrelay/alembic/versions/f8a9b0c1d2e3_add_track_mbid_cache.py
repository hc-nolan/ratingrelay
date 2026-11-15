"""add_track_mbid_cache

Revision ID: f8a9b0c1d2e3
Revises: d2e3f4a5b6c7
Create Date: 2026-11-13 03:00:00.000000

The recording MBID used to live only on ``syncrecord``, which is the row a reset
deletes. That made a reset throw away every expensive MusicBrainz lookup with it
(~16 minutes for a cold 361-track run, 35 seconds warm). The MBID is a property
of the track, not of a write, so it gets its own table and outlives the reset.

Existing rows are backfilled from ``syncrecord`` so the first run after upgrade
is already warm. ``syncrecord`` stores only the normalized key, so the backfill
can recover the key but not the display artist/title; the next real run fills
those in properly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy import text


revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# One row per key: the live database records the same track for both targets.
_BACKFILL = """
INSERT INTO trackmbid (id, normalized_key, artist, title, recording_mbid, resolved_at)
SELECT lower(hex(randomblob(16))), s.normalized_key, s.normalized_key, '',
       max(s.recording_mbid), max(s.synced_at)
  FROM syncrecord s
 WHERE s.recording_mbid IS NOT NULL
   AND s.recording_mbid <> ''
 GROUP BY s.normalized_key
"""


def upgrade() -> None:
    op.create_table(
        'trackmbid',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), primary_key=True, nullable=False),
        sa.Column('normalized_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('artist', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('recording_mbid', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_trackmbid_normalized_key', 'trackmbid', ['normalized_key'], unique=True)
    op.execute(text(_BACKFILL))


def downgrade() -> None:
    op.drop_index('ix_trackmbid_normalized_key', table_name='trackmbid')
    op.drop_table('trackmbid')
