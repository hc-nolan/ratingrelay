"""add_sync_intent

Revision ID: c1d2e3f4a5b6
Revises: a8b9c0d1e2f3
Create Date: 2026-11-13 00:00:00.000000

Records *what* was written to a target, not just that something was: the intent
(love/hate/rating:N), the rating value, and the target-side identity needed to
undo it. Existing rows keep working; their intent is backfilled from the target
service, because v2 wrote exactly one thing per target and that is recoverable
from the row itself. Rows that predate ``target_key`` cannot be undone and are
left alone by the reconciler.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'a8b9c0d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# v2 wrote one kind of thing per target: a love on Last.fm, the configured
# rating (10.0 by default) on Plex, and a love *or* a hate on ListenBrainz.
# ListenBrainz is the one case that cannot be inferred, so it stays NULL.
_BACKFILL_INTENT = """
UPDATE syncrecord
   SET intent = CASE target_service
                  WHEN 'lastfm' THEN 'love'
                  WHEN 'plex' THEN 'rating:10.0'
                  ELSE NULL
                END
 WHERE intent IS NULL
"""

# Feedback is keyed by recording MBID, so rows that recorded one can be undone
# even though they predate target_key. Plex rows cannot: undoing them needs the
# rating keys, which were never stored.
_BACKFILL_LBZ_TARGET_KEY = """
UPDATE syncrecord
   SET target_key = recording_mbid
 WHERE target_service = 'listenbrainz'
   AND recording_mbid IS NOT NULL
"""


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    if not _column_exists("syncrecord", "intent"):
        op.add_column("syncrecord", sa.Column("intent", sa.String(), nullable=True))
    if not _column_exists("syncrecord", "rating"):
        op.add_column("syncrecord", sa.Column("rating", sa.Float(), nullable=True))
    if not _column_exists("syncrecord", "target_key"):
        op.add_column("syncrecord", sa.Column("target_key", sa.String(), nullable=True))
    op.execute(text(_BACKFILL_INTENT))
    op.execute(text(_BACKFILL_LBZ_TARGET_KEY))


def downgrade() -> None:
    with op.batch_alter_table("syncrecord") as batch_op:
        batch_op.drop_column("target_key")
        batch_op.drop_column("rating")
        batch_op.drop_column("intent")
