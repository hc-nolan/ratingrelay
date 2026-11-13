"""add_cancel_requested

Revision ID: b1c2d3e4f5a6
Revises: f8a9b0c1d2e3
Create Date: 2026-11-13 03:40:00.000000

A run in progress could not be stopped: ``DELETE /api/relay/jobs/{id}`` refused
anything that was already running. A run must not be interrupted mid-write — its
whole value is that every write it made is recorded — so cancellation is a flag
the worker observes at a track boundary rather than a kill.

The flag stays set after the job ends, so "stopped early" remains readable from
the row instead of only from the log.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'f8a9b0c1d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'job',
        sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    with op.batch_alter_table('job') as batch:
        batch.drop_column('cancel_requested')
