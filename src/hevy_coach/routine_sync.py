"""Compare a gym card with a saved Hevy routine and prepare safe updates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .api_sync import KG_TO_LBS
from .coach import _matches
from .gym_card import CardItem, CardSet
from .hevy_api import HevyAPI, HevyAPIError
from .models import ExercisePolicy, RoutinePolicy


@dataclass(frozen=True)
class RoutineDifference:
    exercise: str
    current: str
    recommended: str
    updatable: bool = True


@dataclass(frozen=True)
class RoutineComparison:
    routine_id: str
    routine_title: str
    differences: tuple[RoutineDifference, ...]
    update_payload: dict[str, Any]

    @property
    def has_updates(self) -> bool:
        return any(item.updatable for item in self.differences)


def _normalized(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _routine_matches(title: str, routine: RoutinePolicy) -> bool:
    return _normalized(title) in {
        _normalized(candidate) for candidate in (routine.title, *routine.aliases)
    }


def _fmt_number(value: float) -> str:
    return f"{value:g}"


def _prescribed_number(value: object) -> float | None:
    """Treat Hevy's zero placeholders as unprescribed routine values."""
    if value is None:
        return None
    number = float(value)
    return number if number > 0 else None


def _compact_sets(sets: list[dict[str, Any]], *, warmup_count: int = 0) -> str:
    if not sets:
        return "no sets"
    values: list[str] = []
    for index, item in enumerate(sets):
        duration = _prescribed_number(item.get("duration_seconds"))
        reps = _prescribed_number(item.get("reps"))
        weight_kg = _prescribed_number(item.get("weight_kg"))
        if duration is not None:
            value = f"{int(duration)}s"
        elif weight_kg is None:
            value = f"{int(reps)} reps" if reps is not None else "unprescribed"
        else:
            weight = round(float(weight_kg) * KG_TO_LBS, 2)
            value = (
                f"{_fmt_number(weight)}×{int(reps)}" if reps is not None else _fmt_number(weight)
            )
        if item.get("type") == "warmup" or index < warmup_count:
            value += " warm-up"
        values.append(value)
    groups: list[tuple[str, int]] = []
    for value in values:
        if groups and groups[-1][0] == value:
            previous, count = groups[-1]
            groups[-1] = (previous, count + 1)
        else:
            groups.append((value, 1))
    return ", ".join(value if count == 1 else f"{value}×{count}" for value, count in groups)


def _desired_set(item: CardSet, *, warmup: bool) -> dict[str, Any]:
    return {
        "type": "warmup" if warmup else "normal",
        "weight_kg": None if item.weight is None else item.weight / KG_TO_LBS,
        "reps": item.reps,
        "distance_meters": None,
        "duration_seconds": item.duration_seconds,
        "custom_metric": None,
    }


def _desired_sets(item: CardItem) -> list[dict[str, Any]]:
    return [
        _desired_set(planned, warmup=index < item.warmup_count)
        for index, planned in enumerate(item.planned_sets)
    ]


def _sets_match(current: list[dict[str, Any]], desired: list[dict[str, Any]]) -> bool:
    if len(current) != len(desired):
        return False
    for actual, target in zip(current, desired, strict=True):
        for key in ("reps", "duration_seconds"):
            if _prescribed_number(actual.get(key)) != _prescribed_number(target[key]):
                return False
        actual_weight = _prescribed_number(actual.get("weight_kg"))
        target_weight = _prescribed_number(target["weight_kg"])
        if actual_weight is None or target_weight is None:
            if actual_weight is not None or target_weight is not None:
                return False
        elif abs(float(actual_weight) - float(target_weight)) > 0.03:
            return False
    return True


def _writable_routine(routine: dict[str, Any]) -> dict[str, Any]:
    set_fields = (
        "type",
        "weight_kg",
        "reps",
        "distance_meters",
        "duration_seconds",
        "custom_metric",
        "rep_range",
    )
    exercises = []
    for exercise in routine.get("exercises", []):
        if not isinstance(exercise, dict):
            continue
        exercises.append(
            {
                "exercise_template_id": exercise.get("exercise_template_id"),
                "superset_id": exercise.get("superset_id", exercise.get("supersets_id")),
                "rest_seconds": exercise.get("rest_seconds"),
                "notes": exercise.get("notes", ""),
                "sets": [
                    {key: deepcopy(item.get(key)) for key in set_fields if key in item}
                    for item in exercise.get("sets", [])
                    if isinstance(item, dict)
                ],
            }
        )
    output = {
        "title": routine.get("title", ""),
        "notes": routine.get("notes", ""),
        "exercises": exercises,
    }
    if "folder_id" in routine:
        output["folder_id"] = routine["folder_id"]
    return output


def compare_routine(
    api: HevyAPI,
    configured_routine: RoutinePolicy,
    card_items: list[CardItem],
    policies: list[ExercisePolicy],
) -> RoutineComparison:
    """Find the matching saved routine and compare its sets with the gym card."""
    summaries = [
        item
        for item in api.iter_routines()
        if _routine_matches(str(item.get("title", "")), configured_routine)
    ]
    if not summaries:
        raise HevyAPIError(f"No saved Hevy routine matches {configured_routine.display_title!r}.")
    if len(summaries) > 1:
        raise HevyAPIError(
            f"Multiple saved Hevy routines match {configured_routine.display_title!r}."
        )
    routine_id = str(summaries[0].get("id", ""))
    if not routine_id:
        raise HevyAPIError("The matching Hevy routine has no ID.")
    routine = api.routine(routine_id)
    writable = _writable_routine(routine)
    card_by_name = {item.canonical_exercise: item for item in card_items}
    policy_by_name = {policy.name: policy for policy in policies}
    found: set[str] = set()
    differences: list[RoutineDifference] = []

    for original, output in zip(routine.get("exercises", []), writable["exercises"], strict=False):
        if not isinstance(original, dict):
            continue
        title = original.get("title")
        if not title and original.get("exercise_template_id"):
            template = api.exercise_template(str(original["exercise_template_id"]))
            title = template.get("title")
        policy = next(
            (candidate for candidate in policies if title and _matches(str(title), candidate)), None
        )
        if policy is None or policy.name not in card_by_name:
            continue
        item = card_by_name[policy.name]
        found.add(policy.name)
        current = original.get("sets", [])
        current = current if isinstance(current, list) else []
        desired = _desired_sets(item)
        for index, target in enumerate(desired):
            if index < len(current) and current[index].get("type"):
                target["type"] = current[index]["type"]
        if not _sets_match(current, desired):
            warmup_count = item.warmup_count
            differences.append(
                RoutineDifference(
                    item.exercise,
                    _compact_sets(current, warmup_count=warmup_count),
                    _compact_sets(desired, warmup_count=warmup_count),
                )
            )
            output["sets"] = desired

    for canonical, item in card_by_name.items():
        if canonical not in found and canonical in policy_by_name:
            differences.append(
                RoutineDifference(
                    item.exercise,
                    "exercise not found in saved routine",
                    _compact_sets(_desired_sets(item), warmup_count=item.warmup_count),
                    updatable=False,
                )
            )

    return RoutineComparison(
        routine_id,
        str(routine.get("title", configured_routine.display_title)),
        tuple(differences),
        writable,
    )


def comparison_json(comparison: RoutineComparison) -> dict[str, Any]:
    return {
        "routine_id": comparison.routine_id,
        "routine_title": comparison.routine_title,
        "matches_gym_card": not comparison.differences,
        "differences": [
            {
                "exercise": item.exercise,
                "hevy": item.current,
                "recommended": item.recommended,
                "updatable": item.updatable,
            }
            for item in comparison.differences
        ],
    }
