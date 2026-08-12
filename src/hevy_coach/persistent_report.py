"""Markdown and JSON views of a persisted latest workout."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict

from .coach import recommend_all, working_sets
from .models import ExercisePolicy, RoutinePolicy, SetRecord


def _weight(value: float | None) -> str:
    if value is None:
        return "bodyweight"
    return f"{value:g} lb"


def _sets(records: list[SetRecord]) -> str:
    return ", ".join(
        f"{_weight(record.weight)} × {record.reps if record.reps is not None else '—'}"
        for record in records
    )


def _group(records: list[SetRecord]) -> OrderedDict[str, list[SetRecord]]:
    grouped: OrderedDict[str, list[SetRecord]] = OrderedDict()
    for record in records:
        grouped.setdefault(record.exercise, []).append(record)
    return grouped


def report_payload(
    records: list[SetRecord],
    policies: list[ExercisePolicy],
    history_records: list[SetRecord] | None = None,
    routine: RoutinePolicy | None = None,
) -> dict:
    if not records:
        raise ValueError("No imported workouts available; run `hevy-coach import PATH` first.")
    first = records[0]
    exercises = []
    for title, sets in _group(records).items():
        policy = next(
            (
                item
                for item in policies
                if title.casefold()
                in {item.name.casefold(), *(alias.casefold() for alias in item.aliases)}
            ),
            None,
        )
        warmup_set_count = routine.warmup_set_count(policy.name) if routine and policy else 0
        working = working_sets(sets, policy.sets if policy else None, warmup_set_count)
        warmups = [item for item in sets if item not in working]
        last_rpe = next((item.rpe for item in reversed(working) if item.rpe is not None), None)
        exercises.append(
            {
                "exercise": title,
                "warmup_sets": [_set_json(item) for item in warmups],
                "working_sets": [_set_json(item) for item in working],
                "last_set_rpe": last_rpe,
            }
        )
    recommendations = [
        {**asdict(item), "action": item.action.value}
        for item in recommend_all(
            history_records or records,
            policies,
            dict(routine.warmup_set_counts) if routine else None,
        )
    ]
    duration = int((first.ended_at - first.started_at).total_seconds()) if first.ended_at else None
    return {
        "workout": {
            "title": first.routine,
            "date": first.started_at.date().isoformat(),
            "start_time": first.started_at.isoformat(),
            "duration_seconds": duration,
            "exercise_count": len(exercises),
            "set_count": len(records),
        },
        "exercises": exercises,
        "recommendations": recommendations,
    }


def _set_json(record: SetRecord) -> dict:
    return {
        "weight_lbs": record.weight,
        "reps": record.reps,
        "distance_miles": record.distance,
        "duration_seconds": record.duration_seconds,
        "rpe": record.rpe,
        "set_type": record.set_type,
    }


def markdown(payload: dict) -> str:
    workout = payload["workout"]
    lines = [
        f"# Hevy Coach — {workout['title']}",
        "",
        f"**Date:** {workout['date']}  ",
        f"**Duration:** {workout['duration_seconds'] // 60 if workout['duration_seconds'] else '—'} min  ",
        f"**Exercises:** {workout['exercise_count']} · **Sets:** {workout['set_count']}",
        "",
        "## Logged sets",
        "",
    ]
    for exercise in payload["exercises"]:
        lines.extend([f"### {exercise['exercise']}", ""])
        if exercise["warmup_sets"]:
            lines.append("Warm-up / ramp-up: " + _json_sets(exercise["warmup_sets"]))
        lines.append("Working: " + _json_sets(exercise["working_sets"]))
        lines.append(
            f"Last-set RPE: {exercise['last_set_rpe'] if exercise['last_set_rpe'] is not None else 'not logged'}"
        )
        lines.append("")
    lines.extend(["## Next session", "", "| Exercise | Action | Recommendation |", "|---|---|---|"])
    for item in payload["recommendations"]:
        lines.append(f"| {item['exercise']} | {item['action']} | {item['message']} |")
    return "\n".join(lines) + "\n"


def _json_sets(items: list[dict]) -> str:
    if not items:
        return "none"
    return ", ".join(
        f"{_weight(item['weight_lbs'])} × {item['reps'] if item['reps'] is not None else '—'}"
        for item in items
    )


def dated_filename(payload: dict) -> str:
    title = payload["workout"]["title"].casefold()
    slug = "".join(character if character.isalnum() else "-" for character in title).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"{payload['workout']['date']}-{slug}.md"
