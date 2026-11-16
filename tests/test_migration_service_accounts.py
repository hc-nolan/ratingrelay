"""The accounts migration keeps an existing database working.

``servicecredential`` held one row per service kind. ``serviceaccount`` holds one
row per *account*, keyed by the ref every sync record already carries — so the
migration must give each existing credential the bare kind as its id, and
nothing else in the database moves. Downgrading is refused once a second account
exists, because the old table cannot represent one.
"""

import os
import pathlib
import sqlite3
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BEFORE = "57eb195c664d"  # the revision that still has servicecredential


def _alembic(database: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE": str(database)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def test_each_existing_credential_becomes_the_first_account_of_its_service(tmp_path):
    database = tmp_path / "old.db"
    _alembic(database, "upgrade", BEFORE)
    with sqlite3.connect(database) as conn:
        # Proof alembic used this file: everything below downgrades its target.
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(BEFORE,)]
        conn.execute(
            "INSERT INTO servicecredential (service, token, username, plex_server_url,"
            " plex_server_name, updated_at) VALUES ('plex', 'token', 'tester',"
            " 'http://plex:32400', 'Attic', '2026-01-01 00:00:00')"
        )
        conn.execute(
            "INSERT INTO servicecredential (service, token, secret, username,"
            " password_hash, updated_at) VALUES ('lastfm', 'key', 'secret', 'tester',"
            " 'hash', '2026-01-02 00:00:00')"
        )

    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as conn:
        rows = conn.execute(
            "SELECT id, service, label, token, plex_server_name FROM serviceaccount"
            " ORDER BY id"
        ).fetchall()
        assert rows == [
            ("lastfm", "lastfm", "Last.fm", "key", None),
            ("plex", "plex", "Plex", "token", "Attic"),
        ]
        # The ref each row is keyed by is the string the history already holds.
        assert conn.execute(
            "SELECT count(*) FROM serviceaccount WHERE id <> CAST(service AS VARCHAR)"
        ).fetchone() == (0,)
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'servicecredential'"
        ).fetchall()

    _alembic(database, "downgrade", BEFORE)
    with sqlite3.connect(database) as conn:
        rows = conn.execute(
            "SELECT service, token, plex_server_name FROM servicecredential ORDER BY service"
        ).fetchall()
        assert rows == [("lastfm", "key", None), ("plex", "token", "Attic")]


def test_downgrading_is_refused_while_a_second_account_exists(tmp_path):
    database = tmp_path / "two-accounts.db"
    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO serviceaccount (id, service, label, token, created_at,"
            " updated_at) VALUES ('lastfm', 'lastfm', 'Last.fm', 'a',"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        conn.execute(
            "INSERT INTO serviceaccount (id, service, label, token, created_at,"
            " updated_at) VALUES ('lastfm:old', 'lastfm', 'Old', 'b',"
            " '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
        )

    result = _alembic(database, "downgrade", BEFORE, check=False)
    assert result.returncode != 0
    assert "cannot represent them" in (result.stderr + result.stdout)
    # And the database is still on head, with both accounts intact.
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() != [
            (BEFORE,)
        ]
        assert conn.execute("SELECT count(*) FROM serviceaccount").fetchone() == (2,)
