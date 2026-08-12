from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from .models import SetRecord

HEADER_ALIASES = {
    "routine": ("title", "routine", "routine_title", "workout_title"),
    "started_at": ("start_time", "started_at", "workout_start_time", "date"),
    "ended_at": ("end_time", "ended_at", "workout_end_time"),
    "description": ("description",),
    "exercise": ("exercise_title", "exercise", "exercise_name"),
    "exercise_notes": ("exercise_notes", "notes"),
    "set_index": ("set_index", "set_number", "set"),
    "set_type": ("set_type", "type"),
    "weight": ("weight_lbs", "weight", "weight_kg"),
    "reps": ("reps", "repetitions"),
    "rpe": ("rpe",),
    "distance": ("distance_miles", "distance"),
    "duration_seconds": ("duration_seconds", "duration"),
}


class HevyCSVError(ValueError):
    """Raised when a CSV cannot be interpreted as a Hevy export."""


def _pick(row: Mapping[str, str], field: str) -> str:
    normalized = {key.strip().lower(): value for key, value in row.items() if key is not None}
    for candidate in HEADER_ALIASES[field]:
        if candidate in normalized:
            return (normalized[candidate] or "").strip()
    return ""


def _number(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError as error:
        raise HevyCSVError(f"Invalid number {value!r}") from error


def _integer(value: str, default: int | None = None) -> int | None:
    number = _number(value)
    return int(number) if number is not None else default


def _date(value: str) -> datetime:
    if not value:
        raise HevyCSVError("Missing workout start time/date")
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except ValueError:
        for pattern in (
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%d %b %Y, %H:%M",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(candidate, pattern).replace(tzinfo=UTC)
            except ValueError:
                pass
    raise HevyCSVError(f"Unsupported date format {value!r}")


def _optional_date(value: str) -> datetime | None:
    return _date(value) if value else None


def read_hevy_csv(path: str | Path) -> list[SetRecord]:
    source = Path(path)
    try:
        handle = source.open("r", encoding="utf-8-sig", newline="")
    except OSError as error:
        raise HevyCSVError(f"Could not read {source}: {error}") from error

    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise HevyCSVError("CSV is empty or has no header")
        normalized_headers = {header.strip().lower() for header in reader.fieldnames}
        required = ("routine", "started_at", "exercise")
        missing = [
            field
            for field in required
            if not any(alias in normalized_headers for alias in HEADER_ALIASES[field])
        ]
        if missing:
            raise HevyCSVError("Missing required Hevy columns: " + ", ".join(missing))

        records: list[SetRecord] = []
        for line, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            try:
                records.append(
                    SetRecord(
                        routine=_pick(row, "routine"),
                        started_at=_date(_pick(row, "started_at")),
                        exercise=_pick(row, "exercise"),
                        set_index=_integer(_pick(row, "set_index"), len(records) + 1) or 0,
                        set_type=_pick(row, "set_type") or "normal",
                        weight=_number(_pick(row, "weight")),
                        reps=_integer(_pick(row, "reps")),
                        rpe=_number(_pick(row, "rpe")),
                        ended_at=_optional_date(_pick(row, "ended_at")),
                        description=_pick(row, "description"),
                        exercise_notes=_pick(row, "exercise_notes"),
                        distance=_number(_pick(row, "distance")),
                        duration_seconds=_integer(_pick(row, "duration_seconds")),
                    )
                )
            except HevyCSVError as error:
                raise HevyCSVError(f"Line {line}: {error}") from error
    return records


def filter_routines(records: Iterable[SetRecord], routines: Iterable[str]) -> list[SetRecord]:
    wanted = {name.casefold() for name in routines}
    return [record for record in records if record.routine.casefold() in wanted]


def filter_start_date(records: Iterable[SetRecord], start_date: date) -> list[SetRecord]:
    """Keep only sets performed on or after the inclusive calendar date."""
    return [record for record in records if record.started_at.date() >= start_date]
