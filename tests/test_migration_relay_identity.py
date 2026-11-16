"""The relay-identity migration keeps an existing database working.

Rows written before relays were told apart must survive as unclaimed (``""``),
so the first relay to load the pair adopts them; an existing job must not need
the new column filled in.
"""

import os
import pathlib
import sqlite3
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BEFORE = "b1c2d3e4f5a6"


def _alembic(database: pathlib.Path, *args: str) -> None:
    env = {**os.environ, "DATABASE": str(database)}
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )


def test_upgrade_leaves_old_rows_unclaimed_and_round_trips(tmp_path):
    database = tmp_path / "old.db"
    _alembic(database, "upgrade", BEFORE)
    with sqlite3.connect(database) as conn:
        # Proof alembic used this file: everything below downgrades its target.
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(BEFORE,)]
        conn.execute(
            "INSERT INTO syncrecord (id, source_service, target_service, normalized_key, synced_at)"
            " VALUES ('1', 'plex', 'lastfm', 'a', '2026-01-01 00:00:00')"
        )

    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT relay FROM syncrecord").fetchall() == [("",)]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(job)")}
        assert "allow_mass_undo" in columns

    _alembic(database, "downgrade", BEFORE)
    with sqlite3.connect(database) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(syncrecord)")}
        assert "relay" not in columns
        assert conn.execute("SELECT count(*) FROM syncrecord").fetchone() == (1,)
