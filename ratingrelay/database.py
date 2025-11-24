from typing import Optional
import sqlite3


class Database:
    def __init__(self, settings):
        self.conn = sqlite3.connect(settings.database)
        self.cursor = self.conn.cursor()
        self.create_tables()

    @staticmethod
    def _validate_table_name(table: str) -> Optional[str]:
        """
        Used to validate that a variable contains a valid table name.
        Only strings returned by this function are passed to the database
        as table names.
        """
        match table:
            case "loved":
                tablename = "loved"
            case "hated":
                tablename = "hated"
            case "reset":
                tablename = "reset"
            case _:
                raise ValueError(f"Unrecognized table name: {table}")
        return tablename

    def create_tables(self):
        self.cursor.execute(
            """
    CREATE TABLE IF NOT EXISTS loved(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recordingId TEXT,
        trackId TEXT UNIQUE,
        title TEXT,
        artist TEXT
    )
           """
        )
        self.cursor.execute(
            """
    CREATE TABLE IF NOT EXISTS hated(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recordingId TEXT,
        trackId TEXT UNIQUE,
        title TEXT,
        artist TEXT
    )
           """
        )
        self.cursor.execute(
            """
    CREATE TABLE IF NOT EXISTS reset(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recordingId TEXT,
        trackId TEXT UNIQUE,
        title TEXT,
        artist TEXT
    )
           """
        )
        self.conn.commit()

    def add_track(
        self,
        title: Optional[str],
        artist: Optional[str],
        track_mbid: Optional[str],
        rec_mbid: Optional[str],
        table: str,
    ):
        """
        Add a track to the database, or ignore if one already exists.
        """
        tablename = self._validate_table_name(table)
        self.cursor.execute(
            f"INSERT OR IGNORE INTO {tablename} (title, artist, trackId, recordingId) VALUES(?, ?, ?, ?)",
            (title, artist, track_mbid, rec_mbid),
        )
        self.conn.commit()

    def delete_by_rec_id(
        self,
        rec_mbid: str,
        table: str,
    ):
        """Delete a track by its recording MBID"""
        tablename = self._validate_table_name(table)
        self.cursor.execute(
            f"DELETE FROM {tablename} WHERE recordingId = ?", (rec_mbid,)
        )
        self.conn.commit()

    def delete_by_id(
        self,
        db_id: int,
        table: str,
    ):
        """Delete a track by its ID (primary key)"""
        tablename = self._validate_table_name(table)
        self.cursor.execute(f"DELETE FROM {tablename} WHERE ID = ?", (db_id,))
        self.conn.commit()

    def query_track(
        self, track_mbid: str, title: str, artist: str, table: str
    ) -> Optional[dict]:
        """
        Check for a matching track in the database table provided
        """
        tablename = self._validate_table_name(table)
        query = f"""
            SELECT id, title, artist, trackId, recordingId
            FROM {tablename}
            WHERE trackId = ? OR (title = ? AND artist = ?)
        """
        result = self.cursor.execute(query, (track_mbid, title, artist))
        matching_entry = result.fetchone()
        return self._make_dict(matching_entry) if matching_entry else None

    def _make_dict(self, db_entry: tuple) -> dict:
        """Turn a tuple from the database into a dict where column names are keys"""

        return {
            "id": db_entry[0],
            "title": db_entry[1],
            "artist": db_entry[2],
            "track_mbid": db_entry[3],
            "rec_mbid": db_entry[4],
        }

    def get_all_tracks(self, table: str) -> list[dict]:
        tablename = self._validate_table_name(table)
        result = self.cursor.execute(
            f"SELECT id, title, artist, trackId, recordingId FROM {tablename}"
        )

        entries = result.fetchall()
        formatted = [self._make_dict(t) for t in entries]
        return formatted
