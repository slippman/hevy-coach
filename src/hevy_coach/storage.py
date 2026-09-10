"""SQLite persistence and versioned schema migrations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .time_utils import as_utc, timezone_name

DEFAULT_DATA_DIR = Path("data")
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "hevy.db"

MIGRATIONS = [
    """
    CREATE TABLE imports (
      id INTEGER PRIMARY KEY,
      source_filename TEXT NOT NULL,
      archived_filename TEXT NOT NULL,
      sha256 TEXT NOT NULL UNIQUE,
      imported_at TEXT NOT NULL,
      workouts_added INTEGER NOT NULL DEFAULT 0,
      exercises_added INTEGER NOT NULL DEFAULT 0,
      sets_added INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE workouts (
      id INTEGER PRIMARY KEY,
      workout_key TEXT NOT NULL UNIQUE,
      title TEXT NOT NULL,
      start_time TEXT NOT NULL,
      end_time TEXT,
      description TEXT NOT NULL DEFAULT '',
      duration_seconds INTEGER,
      created_at TEXT NOT NULL
    );
    CREATE TABLE exercises (
      id INTEGER PRIMARY KEY,
      workout_id INTEGER NOT NULL REFERENCES workouts(id),
      exercise_title TEXT NOT NULL,
      exercise_notes TEXT NOT NULL DEFAULT '',
      exercise_order INTEGER NOT NULL,
      UNIQUE(workout_id, exercise_title, exercise_order)
    );
    CREATE TABLE sets (
      id INTEGER PRIMARY KEY,
      set_key TEXT NOT NULL UNIQUE,
      exercise_id INTEGER NOT NULL REFERENCES exercises(id),
      set_index INTEGER NOT NULL,
      set_type TEXT NOT NULL,
      weight_lbs REAL,
      reps INTEGER,
      distance_miles REAL,
      duration_seconds INTEGER,
      rpe REAL
    );
    CREATE INDEX idx_workouts_start_time ON workouts(start_time);
    CREATE INDEX idx_exercises_title ON exercises(exercise_title);
    """,
    """
    ALTER TABLE workouts ADD COLUMN source_provider TEXT NOT NULL DEFAULT 'csv';
    ALTER TABLE workouts ADD COLUMN source_id TEXT;
    CREATE UNIQUE INDEX idx_workouts_source_id
      ON workouts(source_provider, source_id)
      WHERE source_id IS NOT NULL;

    CREATE TABLE sync_state (
      provider TEXT PRIMARY KEY,
      cursor TEXT NOT NULL,
      synced_at TEXT NOT NULL
    );
    """,
]

UTC_TIMESTAMP_MIGRATION = 3
EXERCISE_METADATA_MIGRATION = 4


def _key(*values: object) -> str:
    payload = json.dumps(values, default=str, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _legacy_csv_as_utc(value: str | None) -> str | None:
    if value is None:
        return None
    mislabeled = datetime.fromisoformat(value)
    offset = mislabeled.utcoffset()
    # The legacy parser attached UTC to offset-free CSV values. A non-zero offset, however,
    # could only have come explicitly from the export and already identifies the correct instant.
    if offset is not None and offset.total_seconds() != 0:
        return as_utc(mislabeled).isoformat()
    wall_clock = mislabeled.replace(tzinfo=None)
    return as_utc(wall_clock, naive_is_local=True).isoformat()


def _migrate_csv_timestamps_to_utc(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    workouts = connection.execute(
        """SELECT id, title, start_time, end_time FROM workouts
           WHERE source_provider = 'csv'"""
    ).fetchall()
    for workout in workouts:
        start_time = _legacy_csv_as_utc(workout["start_time"])
        end_time = _legacy_csv_as_utc(workout["end_time"])
        workout_natural_key = _key(workout["title"], start_time, end_time)
        sets = connection.execute(
            """SELECT s.id, e.exercise_title, s.set_index, s.set_type, s.weight_lbs, s.reps,
                      s.distance_miles, s.duration_seconds, s.rpe
               FROM sets s JOIN exercises e ON e.id = s.exercise_id
               WHERE e.workout_id = ?""",
            (workout["id"],),
        ).fetchall()
        for workout_set in sets:
            connection.execute(
                "UPDATE sets SET set_key = ? WHERE id = ?",
                (
                    _key(
                        workout_natural_key,
                        workout_set["exercise_title"],
                        workout_set["set_index"],
                        workout_set["set_type"],
                        workout_set["weight_lbs"],
                        workout_set["reps"],
                        workout_set["distance_miles"],
                        workout_set["duration_seconds"],
                        workout_set["rpe"],
                    ),
                    workout_set["id"],
                ),
            )
        connection.execute(
            """UPDATE workouts SET workout_key = ?, start_time = ?, end_time = ? WHERE id = ?""",
            (workout_natural_key, start_time, end_time, workout["id"]),
        )
    connection.execute(
        """INSERT INTO app_metadata(key, value) VALUES ('legacy_csv_timezone', ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (timezone_name(),),
    )


def _add_exercise_metadata(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        ALTER TABLE exercises ADD COLUMN exercise_template_id TEXT;
        ALTER TABLE exercises ADD COLUMN exercise_type TEXT;
        ALTER TABLE exercises ADD COLUMN superset_id INTEGER;
        CREATE INDEX idx_exercises_template_id ON exercises(exercise_template_id);

        CREATE TABLE exercise_templates (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          type TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )


def connect(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_version")}
    for version, sql in enumerate(MIGRATIONS, start=1):
        if version not in applied:
            with connection:
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, datetime('now'))",
                    (version,),
                )
    if UTC_TIMESTAMP_MIGRATION not in applied:
        with connection:
            _migrate_csv_timestamps_to_utc(connection)
            connection.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, datetime('now'))",
                (UTC_TIMESTAMP_MIGRATION,),
            )
    if EXERCISE_METADATA_MIGRATION not in applied:
        with connection:
            _add_exercise_metadata(connection)
            connection.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, datetime('now'))",
                (EXERCISE_METADATA_MIGRATION,),
            )


@contextmanager
def database(path: str | Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        migrate(connection)
        yield connection
    finally:
        connection.close()
