"""add_dry_run_to_job

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-05-11 00:00:00.000000

Adds dry_run column to the job table. Also adds tracks_skipped for installs
where the add_jobs_tables migration predates that column being added to the model.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision: str = 'a8b9c0d1e2f3'
down_revision: Union[str, Sequence[str], None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    if not _column_exists("job", "tracks_skipped"):
        op.add_column("job", sa.Column("tracks_skipped", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("job", sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("job") as batch_op:
        batch_op.drop_column("dry_run")
