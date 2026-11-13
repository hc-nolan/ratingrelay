"""add_typed_outcomes

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-11-13 01:00:00.000000

Track events stop being judged by their error *string*. Each event gains the
engine's typed outcome, the reason, and (for an ambiguous match) the candidate
list. Existing events are backfilled from the two error strings v2 produced, so
a history written before this migration still shows up in the unmatched queue.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BACKFILL_REASON = """
UPDATE trackevent
   SET reason = CASE error
                  WHEN 'No match found in Plex library' THEN 'no_match'
                  WHEN 'No MusicBrainz recording ID found' THEN 'no_recording_mbid'
                END,
       outcome = 'unmatched'
 WHERE reason IS NULL
   AND error IN ('No match found in Plex library',
                 'No MusicBrainz recording ID found')
"""


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    for column in ("outcome", "reason", "candidates"):
        if not _column_exists("trackevent", column):
            op.add_column("trackevent", sa.Column(column, sa.String(), nullable=True))
    op.execute(text(_BACKFILL_REASON))


def downgrade() -> None:
    with op.batch_alter_table("trackevent") as batch_op:
        batch_op.drop_column("candidates")
        batch_op.drop_column("reason")
        batch_op.drop_column("outcome")
