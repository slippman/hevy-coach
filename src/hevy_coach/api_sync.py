"""Incremental synchronization from Hevy's public API into SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .importer import set_key, workout_key
from .models import SetRecord
from .time_utils import as_utc, local_date

PROVIDER = "hevy_api"
KG_TO_LBS = 2.2046226218487757
METERS_TO_MILES = 0.000621371192237334
CSV_MATCH_TOLERANCE = timedelta(minutes=5)


class WorkoutEventSource(Protocol):
    def iter_workout_events(self, since: str) -> Iterable[dict[str, Any]]: ...

    def exercise_template(self, template_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SyncResult:
    since: datetime
    synced_at: datetime
    workouts_added: int
    workouts_updated: int
    workouts_deleted: int

    @property
    def changed(self) -> int:
        return self.workouts_added + self.workouts_updated + self.workouts_deleted


def _text(value: object, field: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"Hevy workout has an invalid {field}")
    return value


def _timestamp(value: object, field: str) -> datetime:
    candidate = _text(value, field, allow_empty=False).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"Hevy workout has an invalid {field}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Hevy workout {field} must include a timezone")
    return as_utc(parsed)


def _optional_timestamp(value: object, field: str) -> datetime | None:
    return None if value is None else _timestamp(value, field)


def _optional_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"Hevy workout has an invalid {field}")
    return float(value)


def _optional_integer(value: object, field: str) -> int | None:
    number = _optional_number(value, field)
    return None if number is None else int(number)


def _index(item: object, field: str) -> float:
    if not isinstance(item, Mapping):
        raise TypeError(f"Hevy workout has an invalid {field}")
    return _optional_number(item.get("index"), f"{field} index") or 0


def workout_records(
    workout: Mapping[str, Any], exercise_types: Mapping[str, str] | None = None
) -> list[SetRecord]:
    """Convert one official API workout payload to the app's normalized set records."""
    title = _text(workout.get("title"), "title", allow_empty=False)
    started_at = _timestamp(workout.get("start_time"), "start_time")
    ended_at = _optional_timestamp(workout.get("end_time"), "end_time")
    description = workout.get("description") or ""
    _text(description, "description")
    exercises = workout.get("exercises")
    if not isinstance(exercises, list):
        raise TypeError("Hevy workout has invalid exercises")

    records: list[SetRecord] = []
    known_types = exercise_types or {}
    for exercise in sorted(exercises, key=lambda item: _index(item, "exercise")):
        if not isinstance(exercise, dict):
            raise TypeError("Hevy workout has an invalid exercise")
        exercise_title = _text(exercise.get("title"), "exercise title", allow_empty=False)
        notes = exercise.get("notes") or ""
        _text(notes, "exercise notes")
        template_id = exercise.get("exercise_template_id")
        if template_id is not None:
            template_id = _text(template_id, "exercise template id", allow_empty=False)
        exercise_type = known_types.get(template_id, "")
        sets = exercise.get("sets")
        if not isinstance(sets, list):
            raise TypeError("Hevy workout exercise has invalid sets")
        for workout_set in sorted(sets, key=lambda item: _index(item, "set")):
            if not isinstance(workout_set, dict):
                raise TypeError("Hevy workout has an invalid set")
            weight_kg = _optional_number(workout_set.get("weight_kg"), "weight_kg")
            distance_meters = _optional_number(
                workout_set.get("distance_meters"), "distance_meters"
            )
            duration_seconds = _optional_integer(
                workout_set.get("duration_seconds"), "duration_seconds"
            )
            reps = _optional_integer(workout_set.get("reps"), "reps")
            unweighted = exercise_type in {"reps_only", "duration"} or (
                exercise_type == "bodyweight_reps" and weight_kg == 0
            )
            duration_only = exercise_type == "duration"
            records.append(
                SetRecord(
                    routine=title,
                    started_at=started_at,
                    ended_at=ended_at,
                    description=description,
                    exercise=exercise_title,
                    exercise_notes=notes,
                    set_index=_optional_integer(workout_set.get("index"), "set index") or 0,
                    set_type=_text(workout_set.get("type") or "normal", "set type"),
                    weight=(
                        None if weight_kg is None or unweighted else round(weight_kg * KG_TO_LBS, 2)
                    ),
                    reps=None if duration_only else reps,
                    distance=(
                        None
                        if distance_meters in {None, 0}
                        else round(distance_meters * METERS_TO_MILES, 6)
                    ),
                    duration_seconds=duration_seconds or None,
                    rpe=_optional_number(workout_set.get("rpe"), "rpe"),
                )
            )
    return records


def _normalized_set_type(value: str) -> str:
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    return "warmup" if normalized in {"warmup", "warm_up", "w"} else normalized


def _record_fingerprint(records: Iterable[SetRecord]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            record.exercise.casefold(),
            _normalized_set_type(record.set_type),
            None if record.weight is None else round(record.weight, 2),
            record.reps,
            None if record.distance is None else round(record.distance, 6),
            record.duration_seconds,
            record.rpe,
        )
        for record in records
    )


def _stored_records(connection: sqlite3.Connection, workout_id: int) -> list[SetRecord]:
    rows = connection.execute(
        """SELECT w.title, w.start_time, w.end_time, w.description,
                  e.exercise_title, e.exercise_notes,
                  s.set_index, s.set_type, s.weight_lbs, s.reps, s.distance_miles,
                  s.duration_seconds, s.rpe
           FROM workouts w
           JOIN exercises e ON e.workout_id = w.id
           JOIN sets s ON s.exercise_id = e.id
           WHERE w.id = ?
           ORDER BY e.exercise_order, s.set_index, s.id""",
        (workout_id,),
    ).fetchall()
    return [
        SetRecord(
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
        for row in rows
    ]


def _matching_csv_workout(connection: sqlite3.Connection, records: list[SetRecord]) -> int | None:
    if not records:
        return None
    first = records[0]
    api_date = local_date(first.started_at)
    wanted = _record_fingerprint(records)
    matches: list[int] = []
    rows = connection.execute(
        """SELECT id, start_time, end_time FROM workouts
           WHERE title = ? COLLATE NOCASE AND source_id IS NULL""",
        (first.routine,),
    ).fetchall()
    for row in rows:
        stored_start = datetime.fromisoformat(row["start_time"])
        if local_date(stored_start) != api_date:
            continue
        if abs(stored_start - first.started_at) > CSV_MATCH_TOLERANCE:
            continue
        stored_end = datetime.fromisoformat(row["end_time"]) if row["end_time"] else None
        if (
            stored_end is not None
            and first.ended_at is not None
            and abs(stored_end - first.ended_at) > CSV_MATCH_TOLERANCE
        ):
            continue
        if _record_fingerprint(_stored_records(connection, row["id"])) == wanted:
            matches.append(row["id"])
    return matches[0] if len(matches) == 1 else None


def _replace_workout(
    connection: sqlite3.Connection,
    workout: Mapping[str, Any],
    now: datetime,
    exercise_types: Mapping[str, str],
) -> bool:
    source_id = _text(workout.get("id"), "id", allow_empty=False)
    records = workout_records(workout, exercise_types)
    title = _text(workout.get("title"), "title", allow_empty=False)
    started_at = _timestamp(workout.get("start_time"), "start_time")
    ended_at = _optional_timestamp(workout.get("end_time"), "end_time")
    description = workout.get("description") or ""
    duration = int((ended_at - started_at).total_seconds()) if ended_at else None
    natural_key = workout_key(records[0]) if records else f"{PROVIDER}:{source_id}"

    existing = connection.execute(
        "SELECT id FROM workouts WHERE source_provider = ? AND source_id = ?",
        (PROVIDER, source_id),
    ).fetchone()
    workout_id = existing["id"] if existing else None
    if workout_id is None and records:
        exact = connection.execute(
            "SELECT id FROM workouts WHERE workout_key = ?", (workout_key(records[0]),)
        ).fetchone()
        workout_id = exact["id"] if exact else _matching_csv_workout(connection, records)

    added = workout_id is None
    if added:
        cursor = connection.execute(
            """INSERT INTO workouts
               (workout_key, title, start_time, end_time, description, duration_seconds,
                created_at, source_provider, source_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                natural_key,
                title,
                started_at.isoformat(),
                ended_at.isoformat() if ended_at else None,
                description,
                duration,
                now.isoformat(),
                PROVIDER,
                source_id,
            ),
        )
        workout_id = cursor.lastrowid
    else:
        connection.execute(
            """UPDATE workouts
               SET workout_key = ?, title = ?, start_time = ?, end_time = ?, description = ?,
                   duration_seconds = ?, source_provider = ?, source_id = ?
               WHERE id = ?""",
            (
                natural_key,
                title,
                started_at.isoformat(),
                ended_at.isoformat() if ended_at else None,
                description,
                duration,
                PROVIDER,
                source_id,
                workout_id,
            ),
        )
        connection.execute(
            "DELETE FROM sets WHERE exercise_id IN (SELECT id FROM exercises WHERE workout_id = ?)",
            (workout_id,),
        )
        connection.execute("DELETE FROM exercises WHERE workout_id = ?", (workout_id,))

    exercises = workout.get("exercises")
    assert isinstance(exercises, list)
    record_offset = 0
    for default_order, exercise in enumerate(
        sorted(exercises, key=lambda item: _index(item, "exercise"))
    ):
        assert isinstance(exercise, dict)
        exercise_title = _text(exercise.get("title"), "exercise title", allow_empty=False)
        notes = exercise.get("notes") or ""
        template_id = exercise.get("exercise_template_id")
        if template_id is not None:
            template_id = _text(template_id, "exercise template id", allow_empty=False)
        exercise_type = exercise_types.get(template_id)
        superset_id = _optional_integer(exercise.get("superset_id"), "superset id")
        sets = exercise.get("sets")
        assert isinstance(sets, list)
        order = _optional_integer(exercise.get("index"), "exercise index")
        exercise_cursor = connection.execute(
            """INSERT INTO exercises
               (workout_id, exercise_title, exercise_notes, exercise_order,
                exercise_template_id, exercise_type, superset_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                workout_id,
                exercise_title,
                notes,
                default_order if order is None else order,
                template_id,
                exercise_type,
                superset_id,
            ),
        )
        exercise_id = exercise_cursor.lastrowid
        for record in records[record_offset : record_offset + len(sets)]:
            connection.execute(
                """INSERT INTO sets
                   (set_key, exercise_id, set_index, set_type, weight_lbs, reps, distance_miles,
                    duration_seconds, rpe) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    set_key(natural_key, record),
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
        record_offset += len(sets)
    return added


def _delete_workout(connection: sqlite3.Connection, source_id: str) -> bool:
    row = connection.execute(
        "SELECT id FROM workouts WHERE source_provider = ? AND source_id = ?",
        (PROVIDER, source_id),
    ).fetchone()
    if row is None:
        return False
    connection.execute(
        "DELETE FROM sets WHERE exercise_id IN (SELECT id FROM exercises WHERE workout_id = ?)",
        (row["id"],),
    )
    connection.execute("DELETE FROM exercises WHERE workout_id = ?", (row["id"],))
    connection.execute("DELETE FROM workouts WHERE id = ?", (row["id"],))
    return True


def _default_since(connection: sqlite3.Connection, now: datetime) -> datetime:
    state = connection.execute(
        "SELECT cursor FROM sync_state WHERE provider = ?", (PROVIDER,)
    ).fetchone()
    if state:
        return _timestamp(state["cursor"], "sync cursor")
    latest = connection.execute("SELECT MAX(start_time) FROM workouts").fetchone()[0]
    if latest:
        parsed = datetime.fromisoformat(latest)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC) - timedelta(days=1)
    return now - timedelta(days=30)


def _template_ids(events: Iterable[Mapping[str, Any]]) -> set[str]:
    template_ids: set[str] = set()
    for event in events:
        workout = event.get("workout")
        if not isinstance(workout, Mapping):
            continue
        exercises = workout.get("exercises")
        if not isinstance(exercises, list):
            continue
        for exercise in exercises:
            if not isinstance(exercise, Mapping):
                continue
            template_id = exercise.get("exercise_template_id")
            if isinstance(template_id, str) and template_id:
                template_ids.add(template_id)
    return template_ids


def _exercise_templates(
    connection: sqlite3.Connection,
    source: WorkoutEventSource,
    events: list[dict[str, Any]],
) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    cached = {
        row["id"]: row["type"]
        for row in connection.execute("SELECT id, type FROM exercise_templates")
    }
    fetched: list[tuple[str, str, str]] = []
    for template_id in sorted(_template_ids(events) - cached.keys()):
        template = source.exercise_template(template_id)
        returned_id = _text(template.get("id"), "exercise template id", allow_empty=False)
        if returned_id != template_id:
            raise ValueError("Hevy returned the wrong exercise template")
        title = _text(template.get("title"), "exercise template title", allow_empty=False)
        exercise_type = _text(template.get("type"), "exercise template type", allow_empty=False)
        cached[template_id] = exercise_type
        fetched.append((template_id, title, exercise_type))
    return cached, fetched


def sync_workouts(
    connection: sqlite3.Connection,
    source: WorkoutEventSource,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> SyncResult:
    """Apply Hevy workout events atomically and advance the local sync cursor."""
    synced_at = (now or datetime.now(UTC)).astimezone(UTC)
    start = (since or _default_since(connection, synced_at)).astimezone(UTC)
    events = list(source.iter_workout_events(start.isoformat().replace("+00:00", "Z")))
    exercise_types, fetched_templates = _exercise_templates(connection, source, events)

    added = updated = deleted = 0
    with connection:
        connection.executemany(
            """INSERT INTO exercise_templates(id, title, type, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET title = excluded.title, type = excluded.type,
                                             updated_at = excluded.updated_at""",
            [(*template, synced_at.isoformat()) for template in fetched_templates],
        )
        for event in reversed(events):
            event_type = event.get("type")
            if event_type == "updated":
                workout = event.get("workout")
                if not isinstance(workout, dict):
                    raise ValueError("Hevy returned an invalid updated workout event")
                if _replace_workout(connection, workout, synced_at, exercise_types):
                    added += 1
                else:
                    updated += 1
            elif event_type == "deleted":
                source_id = _text(event.get("id"), "deleted workout id", allow_empty=False)
                deleted += int(_delete_workout(connection, source_id))
            else:
                raise ValueError("Hevy returned an unknown workout event type")
        connection.execute(
            """INSERT INTO sync_state(provider, cursor, synced_at) VALUES (?, ?, ?)
               ON CONFLICT(provider) DO UPDATE SET cursor = excluded.cursor,
                                                   synced_at = excluded.synced_at""",
            (PROVIDER, synced_at.isoformat(), synced_at.isoformat()),
        )
    return SyncResult(start, synced_at, added, updated, deleted)
