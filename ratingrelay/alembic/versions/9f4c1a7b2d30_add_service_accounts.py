"""one account per service, addressed by ref

Revision ID: 9f4c1a7b2d30
Revises: 57eb195c664d
Create Date: 2026-11-16 02:58:11.402118

``servicecredential`` was keyed by the service *kind*, so a kind named exactly one
account. ``serviceaccount`` is keyed by the account's **ref** instead — the string
every sync record, mapping, unmatched row and job config already holds — with the
bare kind reserved for the account that already exists. That is why no other table
moves: ``plex``, ``lastfm`` and ``listenbrainz`` still name the same accounts they
named before.

``label`` is the display name; the ref stays immutable. ``deleted_at`` lets an
account be removed without its ref ever being reissued, and the tombstone holds no
secret material.

Downgrading is refused while a second account of any service exists: the old table
cannot represent one, and dropping rows silently would strand the history written
under their refs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '9f4c1a7b2d30'
down_revision: Union[str, Sequence[str], None] = '57eb195c664d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KIND = sa.Enum('plex', 'lastfm', 'listenbrainz', name='servicename')

# The label a migrated account gets, so a database upgraded in place reads the
# same as a fresh install in the web app.
_LABEL = (
    "CASE service"
    " WHEN 'plex' THEN 'Plex'"
    " WHEN 'lastfm' THEN 'Last.fm'"
    " ELSE 'ListenBrainz' END"
)


def upgrade() -> None:
    op.create_table(
        'serviceaccount',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('service', _KIND, nullable=False),
        sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('token', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('username', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('secret', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('password_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('plex_server_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('plex_server_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_serviceaccount_service', 'serviceaccount', ['service'], unique=False
    )

    # One row per credential, under the bare kind — the ref it was already
    # standing in for. ``updated_at`` becomes ``created_at`` so a database that
    # had one credential per service keeps its order.
    op.execute(
        sa.text(
            f"""
            INSERT INTO serviceaccount (
                id, service, label, token, username, secret, password_hash,
                plex_server_url, plex_server_name, created_at, updated_at
            )
            SELECT
                CAST(service AS VARCHAR), service, {_LABEL}, token, username, secret,
                password_hash, plex_server_url, plex_server_name, updated_at, updated_at
            FROM servicecredential
            """
        )
    )
    op.drop_table('servicecredential')


def downgrade() -> None:
    connection = op.get_bind()
    secondaries = connection.execute(
        sa.text(
            "SELECT count(*) FROM serviceaccount "
            "WHERE id <> CAST(service AS VARCHAR)"
        )
    ).scalar_one()
    if secondaries:
        raise RuntimeError(
            f"{secondaries} account(s) beyond the first of their service exist; "
            "servicecredential cannot represent them"
        )

    op.create_table(
        'servicecredential',
        sa.Column('service', _KIND, nullable=False),
        sa.Column('token', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('username', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('secret', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('password_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('plex_server_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('plex_server_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('service'),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO servicecredential (
                service, token, username, secret, password_hash,
                plex_server_url, plex_server_name, updated_at
            )
            SELECT
                service, token, username, secret, password_hash,
                plex_server_url, plex_server_name, updated_at
            FROM serviceaccount
            WHERE deleted_at IS NULL
            """
        )
    )
    op.drop_index('ix_serviceaccount_service', table_name='serviceaccount')
    op.drop_table('serviceaccount')
