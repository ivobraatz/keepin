from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class State:
    """SQLite-backed registry of every pin keepin has seen."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS pins (
                pin_id       TEXT    PRIMARY KEY,
                board_id     TEXT    NOT NULL,
                board_name   TEXT    NOT NULL,
                local_path   TEXT,
                url          TEXT,
                saved_at     TEXT,
                error        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_board ON pins (board_id);
        """)
        self._con.commit()

    # ------------------------------------------------------------------

    def is_done(self, pin_id: str) -> bool:
        """True if pin was successfully downloaded (or permanently skipped)."""
        row = self._con.execute(
            "SELECT 1 FROM pins WHERE pin_id = ? AND error IS NULL AND local_path IS NOT NULL",
            (pin_id,),
        ).fetchone()
        return row is not None

    def known_ids(self, board_id: str) -> set[str]:
        """All pin_ids we already recorded for this board (downloaded or error)."""
        rows = self._con.execute(
            "SELECT pin_id FROM pins WHERE board_id = ?", (board_id,)
        ).fetchall()
        return {r["pin_id"] for r in rows}

    def mark_ok(self, pin_id: str, board_id: str, board_name: str, path: Path, url: str):
        self._upsert(pin_id, board_id, board_name, str(path), url, None)

    def mark_error(self, pin_id: str, board_id: str, board_name: str, url: str, err: str):
        self._upsert(pin_id, board_id, board_name, None, url, err)

    def _upsert(self, pin_id, board_id, board_name, path, url, error):
        self._con.execute(
            """INSERT OR REPLACE INTO pins
               (pin_id, board_id, board_name, local_path, url, saved_at, error)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (pin_id, board_id, board_name, path, url,
             datetime.now(timezone.utc).isoformat(), error),
        )
        self._con.commit()

    # ------------------------------------------------------------------

    def stats(self) -> dict:
        row = self._con.execute("""
            SELECT
                COUNT(*)                           AS total,
                COUNT(local_path)                  AS downloaded,
                SUM(error IS NOT NULL)             AS errors
            FROM pins
        """).fetchone()
        return dict(row)

    def board_stats(self) -> list[dict]:
        rows = self._con.execute("""
            SELECT
                board_name,
                COUNT(*)            AS total,
                COUNT(local_path)   AS downloaded
            FROM pins
            GROUP BY board_id
            ORDER BY board_name
        """).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self._con.close()
