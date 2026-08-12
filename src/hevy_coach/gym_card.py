"""Compact, phone-friendly next-session cards."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from .coach import _matches, working_sets
from .models import ExercisePolicy, RoutinePolicy, SetRecord


@dataclass(frozen=True)
class CardItem:
    exercise: str
    weight: float | None
    prescription: str
    warmup: str | None = None
    planned_sets: tuple[CardSet, ...] = ()
    history_status: str = "established"


@dataclass(frozen=True)
class CardSet:
    number: int
    weight: float | None
    reps: int | None


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


def _set_target(sets: list[SetRecord], policy: ExercisePolicy, history_status: str) -> str:
    reps = [record.reps for record in sets if record.reps is not None]
    if not reps:
        return "Hold weight · repeat target"
    if history_status == "limited":
        return f"{len(reps)}×{reps[0]}" if len(set(reps)) == 1 else "/".join(map(str, reps))
    last_rpe = next((record.rpe for record in reversed(sets) if record.rpe is not None), None)
    # Ramped loads: use the final load as the consistent baseline next session.
    if len({record.weight for record in sets}) > 1:
        return f"{policy.sets}×{reps[-1]}"
    if last_rpe is not None and last_rpe >= 9.5:
        if reps[-1] < policy.rep_min:
            return "/".join(str(max(rep, policy.rep_min)) for rep in reps)
        if all(rep >= policy.rep_max for rep in reps):
            return f"{policy.sets}×{policy.rep_max}"
    if all(rep == reps[0] for rep in reps):
        if last_rpe is not None and last_rpe <= 7 and policy.rep_max > reps[0]:
            return f"{policy.sets}×{policy.rep_max}"
        return f"{policy.sets}×{reps[0] + 1}"
    target = list(reps)
    lowest = min(range(len(target)), key=target.__getitem__)
    target[lowest] += 1
    return "/".join(str(rep) for rep in target)


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


def _target_reps(prescription: str, policy: ExercisePolicy) -> list[int]:
    if "×" in prescription:
        count, reps = prescription.split("×", maxsplit=1)
        if count.isdigit() and reps.isdigit():
            return [int(reps)] * int(count)
    parts = prescription.split("/")
    if len(parts) > 1 and all(part.isdigit() for part in parts):
        return [int(part) for part in parts]
    return [policy.rep_min] * policy.sets


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
    for canonical in desired_order:
        matches = next(
            (
                sets
                for name, sets in by_title.items()
                if (policy := _policy(name, policies)) and policy.name == canonical
            ),
            None,
        )
        policy = next((item for item in policies if item.name == canonical), None)
        if policy is None or not matches:
            continue
        history_status = (
            "limited"
            if len({record.started_at for record in records if _matches(record.exercise, policy)})
            <= 1
            else "established"
        )
        selected = working_sets(matches, policy.sets)
        if not selected:
            continue
        weight = next((item.weight for item in reversed(selected) if item.weight is not None), None)
        ramp_up = [item for item in matches if item not in selected]
        warmup = None
        warmup_set = None
        if routine and canonical in routine.warmup_exercises and ramp_up:
            first = ramp_up[0]
            if first.weight is not None and first.reps is not None:
                warmup = f"{first.weight:g} lb × {first.reps}"
                warmup_set = CardSet(1, first.weight, first.reps)
        prescription = _set_target(selected, policy, history_status)
        working_set_offset = 1 if warmup_set else 0
        planned_working_sets = tuple(
            CardSet(number + working_set_offset, weight, reps)
            for number, reps in enumerate(_target_reps(prescription, policy), start=1)
        )
        items.append(
            CardItem(
                policy.display_name or canonical,
                weight,
                prescription,
                warmup,
                ((warmup_set,) if warmup_set else ()) + planned_working_sets,
                history_status,
            )
        )
    display = routine.display_title if routine else title
    return display, items


def render_card(title: str, items: list[CardItem]) -> str:
    blocks = [title]
    for item in items:
        lines = [item.exercise, "SET   LBS   REPS"]
        for planned_set in item.planned_sets:
            weight = "—" if planned_set.weight is None else f"{planned_set.weight:g}"
            reps = "—" if planned_set.reps is None else str(planned_set.reps)
            lines.append(f"{planned_set.number:<5} {weight:<5} {reps}")
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
                "sets": [
                    {
                        "set": planned_set.number,
                        "weight_lbs": planned_set.weight,
                        "reps": planned_set.reps,
                    }
                    for planned_set in item.planned_sets
                ],
            }
            for item in items
        ],
        "unknown_exercises": list(unknown_exercises),
    }
