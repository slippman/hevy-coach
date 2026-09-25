"""Compact, phone-friendly next-session cards."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import date, datetime

from .coach import _matches, exercise_decision, working_sets
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
    reasoning_category: str = ""
    explanation: str = ""
    last_weight: float | None = None
    last_reps: tuple[int, ...] = ()
    last_durations: tuple[int, ...] = ()
    last_rpe: float | None = None
    rep_min: int | None = None
    rep_max: int | None = None


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
                rounds = max(
                    routine.working_set_count(name, policy_by_name[name].sets)
                    for name in superset.exercises
                )
                superset_labels[superset.exercises[0]] = (
                    f"SUPERSET · {rounds} ROUNDS · {' → '.join(names)} · "
                    f"REST {superset.rest_min_seconds}–{superset.rest_max_seconds} SEC"
                )
    for canonical in desired_order:
        policy = next((item for item in policies if item.name == canonical), None)
        if policy is None:
            continue
        if routine:
            policy = replace(policy, sets=routine.working_set_count(canonical, policy.sets))
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
        decision = exercise_decision(records, policy, warmup_set_count)
        if policy.progression == "duration":
            weight = decision.target_weight
            target_reps: tuple[int, ...] = ()
            target_durations = decision.target_durations
            prescription = "/".join(f"{value}s" for value in target_durations)
        else:
            weight = decision.target_weight
            target_reps = decision.target_reps
            target_durations = ()
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
                decision.reasoning_category.value,
                decision.explanation,
                decision.last_weight,
                decision.last_reps,
                decision.last_durations,
                decision.last_rpe,
                decision.rep_min,
                decision.rep_max,
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
    explain: bool = False,
    warn_if_old: bool = True,
) -> str:
    blocks = [title]
    if source_date is not None:
        freshness, stale = freshness_line(
            source_date, today or local_date(datetime.now().astimezone())
        )
        blocks.append(("⚠ " if stale and warn_if_old else "") + freshness)
    if explain:
        blocks.append("COACH'S SUMMARY\n" + "\n".join(_coach_summary(items)))
    blocks.append(_workout_table(items))
    section_labels = list(dict.fromkeys(item.section_label for item in items if item.section_label))
    if section_labels:
        blocks.append("\n".join(section_labels))
    return "\n\n".join(blocks) + "\n"


def _join_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _compact_values(values: tuple[int, ...], suffix: str = "") -> str:
    if not values:
        return "—"
    if len(set(values)) == 1:
        return f"{values[0]}{suffix}×{len(values)}"
    return "/".join(f"{value}{suffix}" for value in values)


def _compact_last(item: CardItem) -> str:
    if item.last_durations:
        return _compact_values(item.last_durations, "s")
    reps = _compact_values(item.last_reps)
    if item.progression == "bodyweight_reps" or item.last_weight is None:
        return f"{reps} reps"
    return f"{item.last_weight:g}×{reps}"


def _compact_target(item: CardItem) -> str:
    working = item.planned_sets[1:] if item.warmup else item.planned_sets
    if item.progression == "duration":
        values = tuple(item.duration_seconds or 0 for item in working)
        return _compact_values(values, "s")
    reps = tuple(item.reps or 0 for item in working)
    compact_reps = _compact_values(reps)
    if item.progression == "bodyweight_reps":
        return f"{compact_reps} reps"
    weights = tuple(item.weight for item in working)
    if weights and all(weight == weights[0] for weight in weights) and weights[0] is not None:
        return f"{weights[0]:g}×{compact_reps}"
    return "/".join(
        f"{planned.weight:g}×{planned.reps}"
        if planned.weight is not None and planned.reps is not None
        else "—"
        for planned in working
    )


def _coach_summary(items: list[CardItem]) -> list[str]:
    lines: list[str] = []
    by_reason: dict[str, list[CardItem]] = {}
    for item in items:
        by_reason.setdefault(item.reasoning_category, []).append(item)

    for item in by_reason.get("WEIGHT_UP", []):
        rpe = "" if item.last_rpe is None else f" at RPE {item.last_rpe:g}"
        ceiling = (
            ""
            if item.rep_min is None or item.rep_max is None
            else f", the top of your {item.rep_min}–{item.rep_max} rep range"
        )
        lines.append(
            f"{item.exercise} moves up to {item.weight:g} lb because you reached "
            f"{_compact_last(item)}{rpe}{ceiling}."
        )

    uniform_holds = [
        item
        for item in by_reason.get("HOLD", [])
        if item.last_reps and len(set(item.last_reps)) == 1 and item.last_rpe is not None
    ]
    grouped_holds: set[str] = set()
    for rpe in dict.fromkeys(item.last_rpe for item in uniform_holds):
        group = [item for item in uniform_holds if item.last_rpe == rpe]
        if len(group) > 1:
            lines.append(
                f"{_join_names([item.exercise for item in group])} stay put because their "
                f"last sets reached RPE {rpe:g}."
            )
            grouped_holds.update(item.exercise for item in group)

    add_reps = by_reason.get("ADD_REPS", [])
    if add_reps:
        verb = "adds" if len(add_reps) == 1 else "add"
        lines.append(
            f"{_join_names([item.exercise for item in add_reps])} {verb} reps while keeping the "
            "same weight and building toward their rep ceilings."
        )

    for item in by_reason.get("CONFIRM", []):
        lines.append(
            f"{item.exercise} repeats {_compact_target(item)} once more before the large jump "
            "in weight."
        )

    for item in by_reason.get("HOLD", []):
        if item.exercise in grouped_holds:
            continue
        rpe = (
            ""
            if item.last_rpe is None
            else f" because the last session reached RPE {item.last_rpe:g}"
        )
        lines.append(f"{item.exercise} repeats {_compact_target(item)}{rpe}.")

    limited = by_reason.get("LIMITED_HISTORY", [])
    if limited:
        verb = "repeats" if len(limited) == 1 else "repeat"
        pronoun = "its" if len(limited) == 1 else "their"
        noun = "baseline" if len(limited) == 1 else "baselines"
        lines.append(
            f"{_join_names([item.exercise for item in limited])} {verb} {pronoun} {noun} because "
            "only one session is available."
        )
    for item in by_reason.get("WEIGHT_DOWN", []):
        lines.append(
            f"{item.exercise} reduces to {_compact_target(item)} after the last set was too hard."
        )
    add_time = by_reason.get("ADD_TIME", [])
    if add_time:
        verb = "adds" if len(add_time) == 1 else "add"
        lines.append(
            f"{_join_names([item.exercise for item in add_time])} {verb} time while keeping the "
            "same exercise setup."
        )
    return lines


def _workout_table(items: list[CardItem]) -> str:
    rows = []
    for item in items:
        warmup = "—"
        if item.warmup and item.planned_sets:
            first = item.planned_sets[0]
            if first.weight is not None and first.reps is not None:
                warmup = f"{first.weight:g}×{first.reps}"
        rows.append((item.exercise, warmup, _compact_target(item)))
    exercise_width = max(len("Exercise"), *(len(row[0]) for row in rows))
    warmup_width = max(len("Warm-up"), *(len(row[1]) for row in rows))
    lines = [
        "WORKOUT",
        f"{'Exercise':{exercise_width}}  {'Warm-up':{warmup_width}}  Working Sets",
    ]
    lines.extend(
        f"{exercise:{exercise_width}}  {warmup:{warmup_width}}  {working}"
        for exercise, warmup, working in rows
    )
    return "\n".join(lines)


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
                "reasoning_category": item.reasoning_category,
                "explanation": item.explanation,
                "last_performance": {
                    "weight_lbs": item.last_weight,
                    "reps": list(item.last_reps),
                    "duration_seconds": list(item.last_durations),
                    "rpe": item.last_rpe,
                },
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
