"""Database queries mapped into the analysis model."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .models import SetRecord


@dataclass(frozen=True)
class WorkoutSummary:
    title: str
    started_at: datetime
    duration_seconds: int | None
    exercise_count: int
    set_count: int


@dataclass(frozen=True)
class WorkoutTypeSummary:
    title: str
    session_count: int
    last_started_at: datetime
    set_count: int


def _record(row: sqlite3.Row) -> SetRecord:
    return SetRecord(
        routine=row["title"],
        started_at=datetime.fromisoformat(row["start_time"]),
        ended_at=datetime.fromisoformat(row["end_time"]) if row["end_time"] else None,
        description=row["description"],
        exercise=row["exercise_title"],
        exercise_notes=row["exercise_notes"],
        set_index=row["set_index"],
        set_type=row["set_type"],
        weight=row["weight_lbs"],
        reps=row["reps"],
        distance=row["distance_miles"],
        duration_seconds=row["duration_seconds"],
        rpe=row["rpe"],
    )


def latest_workout_records(connection: sqlite3.Connection) -> list[SetRecord]:
    workout = connection.execute(
        "SELECT * FROM workouts ORDER BY start_time DESC LIMIT 1"
    ).fetchone()
    if workout is None:
        return []
    rows = connection.execute(
        """SELECT w.title, w.start_time, w.end_time, w.description, e.exercise_title, e.exercise_notes,
        e.exercise_order, s.set_index, s.set_type, s.weight_lbs, s.reps, s.distance_miles,
        s.duration_seconds, s.rpe FROM workouts w JOIN exercises e ON e.workout_id = w.id
        JOIN sets s ON s.exercise_id = e.id WHERE w.id = ? ORDER BY e.exercise_order, s.set_index""",
        (workout["id"],),
    ).fetchall()
    return [_record(row) for row in rows]


def all_records(connection: sqlite3.Connection) -> list[SetRecord]:
    rows = connection.execute(
        """SELECT w.title, w.start_time, w.end_time, w.description, e.exercise_title, e.exercise_notes,
        e.exercise_order, s.set_index, s.set_type, s.weight_lbs, s.reps, s.distance_miles,
        s.duration_seconds, s.rpe FROM workouts w JOIN exercises e ON e.workout_id = w.id
        JOIN sets s ON s.exercise_id = e.id ORDER BY w.start_time, e.exercise_order, s.set_index"""
    ).fetchall()
    return [_record(row) for row in rows]


def workout_titles(connection: sqlite3.Connection) -> list[str]:
    return [
        row["title"]
        for row in connection.execute(
            "SELECT title FROM workouts GROUP BY title ORDER BY MAX(start_time) DESC"
        )
    ]


def recent_workouts(connection: sqlite3.Connection, limit: int) -> list[WorkoutSummary]:
    rows = connection.execute(
        """SELECT w.title, w.start_time, w.duration_seconds, COUNT(DISTINCT e.id) AS exercise_count,
        COUNT(s.id) AS set_count FROM workouts w
        LEFT JOIN exercises e ON e.workout_id = w.id
        LEFT JOIN sets s ON s.exercise_id = e.id
        GROUP BY w.id ORDER BY w.start_time DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        WorkoutSummary(
            title=row["title"],
            started_at=datetime.fromisoformat(row["start_time"]),
            duration_seconds=row["duration_seconds"],
            exercise_count=row["exercise_count"],
            set_count=row["set_count"],
        )
        for row in rows
    ]


def workout_types(connection: sqlite3.Connection) -> list[WorkoutTypeSummary]:
    rows = connection.execute(
        """SELECT w.title, COUNT(DISTINCT w.id) AS session_count, MAX(w.start_time) AS last_start_time,
        COUNT(s.id) AS set_count FROM workouts w
        LEFT JOIN exercises e ON e.workout_id = w.id
        LEFT JOIN sets s ON s.exercise_id = e.id
        GROUP BY w.title ORDER BY MAX(w.start_time) DESC"""
    ).fetchall()
    return [
        WorkoutTypeSummary(
            title=row["title"],
            session_count=row["session_count"],
            last_started_at=datetime.fromisoformat(row["last_start_time"]),
            set_count=row["set_count"],
        )
        for row in rows
    ]


def records_for_workout(connection: sqlite3.Connection, title: str) -> list[SetRecord]:
    return records_for_workouts(connection, [title])


def records_for_workouts(
    connection: sqlite3.Connection, titles: list[str] | tuple[str, ...]
) -> list[SetRecord]:
    placeholders = ", ".join("?" for _ in titles)
    rows = connection.execute(
        """SELECT w.title, w.start_time, w.end_time, w.description, e.exercise_title, e.exercise_notes,
        e.exercise_order, s.set_index, s.set_type, s.weight_lbs, s.reps, s.distance_miles,
        s.duration_seconds, s.rpe FROM workouts w JOIN exercises e ON e.workout_id = w.id
        JOIN sets s ON s.exercise_id = e.id WHERE w.title IN ("""
        + placeholders
        + ") ORDER BY w.start_time, e.exercise_order, s.set_index",
        tuple(titles),
    ).fetchall()
    return [_record(row) for row in rows]


def exercise_history(connection: sqlite3.Connection, exercises: list[str]) -> list[SetRecord]:
    placeholders = ", ".join("?" for _ in exercises)
    rows = connection.execute(
        f"""SELECT w.title, w.start_time, w.end_time, w.description, e.exercise_title, e.exercise_notes,
        e.exercise_order, s.set_index, s.set_type, s.weight_lbs, s.reps, s.distance_miles,
        s.duration_seconds, s.rpe FROM workouts w JOIN exercises e ON e.workout_id = w.id
        JOIN sets s ON s.exercise_id = e.id WHERE lower(e.exercise_title) IN ({placeholders})
        ORDER BY w.start_time DESC, s.set_index""",
        tuple(exercise.casefold() for exercise in exercises),
    ).fetchall()
    return [_record(row) for row in rows]
