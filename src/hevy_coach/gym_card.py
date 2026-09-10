"""Compact, phone-friendly next-session cards."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime

from .coach import _matches, next_duration_target, next_session_target, working_sets
from .models import ExercisePolicy, RoutinePolicy, SetRecord
from .time_utils import local_date


@dataclass(frozen=True)
class CardItem:
    exercise: str
    weight: float | None
    prescription: str
    warmup: str | None = None
    planned_sets: tuple[CardSet, ...] = ()
    history_status: str = "established"
    source_date: date | None = None
    progression: str = "weighted_reps"
    section_label: str | None = None


@dataclass(frozen=True)
class CardSet:
    number: int
    weight: float | None
    reps: int | None
    duration_seconds: int | None = None


def latest_session(records: list[SetRecord]) -> list[SetRecord]:
    if not records:
        return []
    latest = max(record.started_at for record in records)
    return [record for record in records if record.started_at == latest]


def _group(records: list[SetRecord]) -> OrderedDict[str, list[SetRecord]]:
    groups: OrderedDict[str, list[SetRecord]] = OrderedDict()
    for record in records:
        groups.setdefault(record.exercise, []).append(record)
    return groups


def _policy(name: str, policies: list[ExercisePolicy]) -> ExercisePolicy | None:
    return next((policy for policy in policies if _matches(name, policy)), None)


def _latest_exercise_session(records: list[SetRecord], policy: ExercisePolicy) -> list[SetRecord]:
    """Return the latest session for one exercise, regardless of routine completeness."""
    matches = [record for record in records if _matches(record.exercise, policy)]
    if not matches:
        return []
    latest = max(record.started_at for record in matches)
    return sorted(
        (record for record in matches if record.started_at == latest),
        key=lambda record: record.set_index,
    )


def unknown_routine_exercises(
    routine: RoutinePolicy | None,
    records: list[SetRecord],
    policies: list[ExercisePolicy],
) -> tuple[str, ...]:
    if routine is None:
        return ()
    unknown = []
    for name in _group(latest_session(records)):
        policy = _policy(name, policies)
        if policy is None or policy.name not in routine.exercises:
            unknown.append(name)
    return tuple(unknown)


def build_card(
    routine: RoutinePolicy | None,
    title: str,
    records: list[SetRecord],
    policies: list[ExercisePolicy],
) -> tuple[str, list[CardItem]]:
    current = latest_session(records)
    by_title = _group(current)
    desired_order = (
        routine.exercises
        if routine
        else tuple(
            policy.name for name in by_title if (policy := _policy(name, policies)) is not None
        )
    )
    items: list[CardItem] = []
    superset_labels: dict[str, str] = {}
    if routine:
        policy_by_name = {policy.name: policy for policy in policies}
        for superset in routine.supersets:
            names = [
                policy_by_name[name].display_name or name
                for name in superset.exercises
                if name in policy_by_name
            ]
            if names:
                rounds = max(policy_by_name[name].sets for name in superset.exercises)
                superset_labels[superset.exercises[0]] = (
                    f"SUPERSET · {rounds} ROUNDS · {' → '.join(names)} · "
                    f"REST {superset.rest_min_seconds}–{superset.rest_max_seconds} SEC"
                )
    for canonical in desired_order:
        policy = next((item for item in policies if item.name == canonical), None)
        if policy is None:
            continue
        matches = (
            _latest_exercise_session(records, policy)
            if routine
            else next(
                (
                    sets
                    for name, sets in by_title.items()
                    if (matched_policy := _policy(name, policies))
                    and matched_policy.name == canonical
                ),
                [],
            )
        )
        if not matches:
            continue
        history_status = (
            "limited"
            if len({record.started_at for record in records if _matches(record.exercise, policy)})
            <= 1
            else "established"
        )
        warmup_set_count = routine.warmup_set_count(canonical) if routine else 0
        selected = working_sets(matches, policy.sets, warmup_set_count)
        if not selected:
            continue
        ramp_up = [item for item in matches if item not in selected]
        warmup = None
        warmup_set = None
        if warmup_set_count and ramp_up:
            first = ramp_up[0]
            if first.weight is not None and first.reps is not None:
                warmup = f"{first.weight:g} lb × {first.reps}"
                warmup_set = CardSet(1, first.weight, first.reps)
        if policy.progression == "duration":
            weight = None
            target_reps: list[int] = []
            target_durations = next_duration_target(selected, policy, history_status)
            prescription = "/".join(f"{value}s" for value in target_durations)
        else:
            weight, target_reps = next_session_target(
                selected, policy, history_status, records, warmup_set_count
            )
            target_durations = []
            prescription = (
                f"{len(target_reps)}×{target_reps[0]}"
                if target_reps and len(set(target_reps)) == 1
                else "/".join(str(rep) for rep in target_reps)
            )
        working_set_offset = 1 if warmup_set else 0
        planned_working_sets = (
            tuple(
                CardSet(number + working_set_offset, None, None, duration)
                for number, duration in enumerate(target_durations, start=1)
            )
            if policy.progression == "duration"
            else tuple(
                CardSet(number + working_set_offset, weight, reps)
                for number, reps in enumerate(target_reps, start=1)
            )
        )
        items.append(
            CardItem(
                policy.display_name or canonical,
                weight,
                prescription,
                warmup,
                ((warmup_set,) if warmup_set else ()) + planned_working_sets,
                history_status,
                local_date(max(item.started_at for item in matches)),
                policy.progression,
                superset_labels.get(canonical),
            )
        )
    display = routine.display_title if routine else title
    return display, items


def oldest_card_source_date(items: list[CardItem]) -> date | None:
    """Return the oldest exercise-session date represented on a gym card."""
    dates = [item.source_date for item in items if item.source_date is not None]
    return min(dates) if dates else None


def freshness_line(source_date: date, today: date) -> tuple[str, bool]:
    age = (today - source_date).days
    days = "today" if age == 0 else f"{age} day{'s' if age != 1 else ''} ago"
    return (
        f"Based on: {source_date.strftime('%b')} {source_date.day}, {source_date.year} ({days})",
        age > 7,
    )


def render_card(
    title: str,
    items: list[CardItem],
    source_date: date | None = None,
    today: date | None = None,
) -> str:
    blocks = [title]
    if source_date is not None:
        freshness, stale = freshness_line(
            source_date, today or local_date(datetime.now().astimezone())
        )
        blocks.append(("⚠ " if stale else "") + freshness)
    for item in items:
        if item.progression == "duration":
            header = "SET   SECONDS"
        elif item.progression == "bodyweight_reps":
            header = "SET   REPS"
        else:
            header = "SET   LBS   REPS"
        lines = [item.exercise, header]
        for planned_set in item.planned_sets:
            if item.progression == "duration":
                duration = planned_set.duration_seconds or 0
                lines.append(f"{planned_set.number:<5} {duration}")
            elif item.progression == "bodyweight_reps":
                reps = "—" if planned_set.reps is None else str(planned_set.reps)
                lines.append(f"{planned_set.number:<5} {reps}")
            else:
                weight = "—" if planned_set.weight is None else f"{planned_set.weight:g}"
                reps = "—" if planned_set.reps is None else str(planned_set.reps)
                lines.append(f"{planned_set.number:<5} {weight:<5} {reps}")
        if item.section_label:
            blocks.append(item.section_label)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def card_json(title: str, items: list[CardItem], unknown_exercises: tuple[str, ...] = ()) -> dict:
    return {
        "workout": title,
        "exercises": [
            {
                "exercise": item.exercise,
                "weight_lbs": item.weight,
                "prescription": item.prescription,
                "warmup": item.warmup,
                "history_status": item.history_status,
                "progression": item.progression,
                "sets": [
                    {
                        "set": planned_set.number,
                        "weight_lbs": planned_set.weight,
                        "reps": planned_set.reps,
                        "duration_seconds": planned_set.duration_seconds,
                    }
                    for planned_set in item.planned_sets
                ],
            }
            for item in items
        ],
        "unknown_exercises": list(unknown_exercises),
    }
