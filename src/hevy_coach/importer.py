"""Idempotent, archival CSV imports into the persistent workout database."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import SetRecord
from .parser import read_hevy_csv


@dataclass(frozen=True)
class ImportResult:
    skipped: bool
    source_hash: str
    archive: Path | None
    workouts_added: int = 0
    exercises_added: int = 0
    sets_added: int = 0


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _key(*values: object) -> str:
    payload = json.dumps(values, default=str, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def workout_key(record: SetRecord) -> str:
    return _key(
        record.routine,
        record.started_at.isoformat(),
        record.ended_at.isoformat() if record.ended_at else None,
    )


def set_key(workout: str, record: SetRecord) -> str:
    return _key(
        workout,
        record.exercise,
        record.set_index,
        record.set_type,
        record.weight,
        record.reps,
        record.distance,
        record.duration_seconds,
        record.rpe,
    )


def _archive(source: Path, imports_dir: Path, digest: str) -> Path:
    imports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = imports_dir / f"{timestamp}-{digest[:12]}-{source.name}"
    shutil.copy2(source, destination)
    return destination


def import_csv(
    connection: sqlite3.Connection, source_path: str | Path, imports_dir: str | Path
) -> ImportResult:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"CSV not found: {source}")
    digest = file_sha256(source)
    if connection.execute("SELECT 1 FROM imports WHERE sha256 = ?", (digest,)).fetchone():
        return ImportResult(skipped=True, source_hash=digest, archive=None)

    records = read_hevy_csv(source)  # Parse before any persistent mutation.
    if not records:
        raise ValueError("The CSV contains no workout sets")
    archive = _archive(source, Path(imports_dir), digest)
    now = datetime.now(UTC).isoformat()
    workouts_added = exercises_added = sets_added = 0
    grouped: OrderedDict[str, list[SetRecord]] = OrderedDict()
    for record in records:
        grouped.setdefault(workout_key(record), []).append(record)

    try:
        with connection:
            for key, workout_records in grouped.items():
                first = workout_records[0]
                duration = (
                    int((first.ended_at - first.started_at).total_seconds())
                    if first.ended_at is not None
                    else None
                )
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO workouts
                    (workout_key, title, start_time, end_time, description, duration_seconds, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        key,
                        first.routine,
                        first.started_at.isoformat(),
                        first.ended_at.isoformat() if first.ended_at else None,
                        first.description,
                        duration,
                        now,
                    ),
                )
                workouts_added += cursor.rowcount
                workout_id = connection.execute(
                    "SELECT id FROM workouts WHERE workout_key = ?", (key,)
                ).fetchone()[0]
                exercises: OrderedDict[str, list[SetRecord]] = OrderedDict()
                for record in workout_records:
                    exercises.setdefault(record.exercise, []).append(record)
                for order, (title, exercise_records) in enumerate(exercises.items()):
                    notes = exercise_records[0].exercise_notes
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO exercises
                        (workout_id, exercise_title, exercise_notes, exercise_order) VALUES (?, ?, ?, ?)""",
                        (workout_id, title, notes, order),
                    )
                    exercises_added += cursor.rowcount
                    exercise_id = connection.execute(
                        """SELECT id FROM exercises WHERE workout_id = ? AND exercise_title = ?
                        AND exercise_order = ?""",
                        (workout_id, title, order),
                    ).fetchone()[0]
                    for record in exercise_records:
                        cursor = connection.execute(
                            """INSERT OR IGNORE INTO sets
                            (set_key, exercise_id, set_index, set_type, weight_lbs, reps, distance_miles,
                             duration_seconds, rpe) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                set_key(key, record),
                                exercise_id,
                                record.set_index,
                                record.set_type,
                                record.weight,
                                record.reps,
                                record.distance,
                                record.duration_seconds,
                                record.rpe,
                            ),
                        )
                        sets_added += cursor.rowcount
            connection.execute(
                """INSERT INTO imports(source_filename, archived_filename, sha256, imported_at,
                workouts_added, exercises_added, sets_added) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    source.name,
                    archive.name,
                    digest,
                    now,
                    workouts_added,
                    exercises_added,
                    sets_added,
                ),
            )
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    return ImportResult(False, digest, archive, workouts_added, exercises_added, sets_added)
