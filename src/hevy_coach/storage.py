"""SQLite persistence and versioned schema migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

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
    """
]


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


@contextmanager
def database(path: str | Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        migrate(connection)
        yield connection
    finally:
        connection.close()
