"""hyperfix as a source: the kind, and the address a bridge account needs

Expand-only: one enum value and one nullable column. Hyperfix accounts carry a URL in
``base_url`` (they are an address, not a well-known service); nothing reads it back for
the older kinds, which keep using ``plex_server_url``.

Revision ID: a1b2c3d4e5f6
Revises: 9f4c1a7b2d30
Create Date: 2026-11-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '9f4c1a7b2d30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A native enum only exists on Postgres; SQLite (the test databases) stores the
    # column as plain text, so there is nothing to widen there.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE servicename ADD VALUE IF NOT EXISTS 'hyperfix'")
    op.add_column(
        'serviceaccount', sa.Column('base_url', sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('serviceaccount', 'base_url')
    # A Postgres enum value cannot be removed; leaving it is harmless.
