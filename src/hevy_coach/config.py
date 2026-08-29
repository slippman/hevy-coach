from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path

from .models import ExercisePolicy, RoutinePolicy

VALID_CATEGORIES = {"compound", "isolation", "core"}


def _positive_int(value: object, label: str) -> int:
    number = int(value)
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def _rep_range(values: dict, label: str) -> tuple[int, int]:
    minimum = _positive_int(values["min_reps"], f"{label}.min_reps")
    maximum = _positive_int(values["max_reps"], f"{label}.max_reps")
    if minimum > maximum:
        raise ValueError(f"{label} has min_reps greater than max_reps")
    return minimum, maximum


def default_config_path() -> Path:
    return Path(str(files("hevy_coach").joinpath("default_config.toml")))


def load_config(path: str | Path | None = None) -> tuple[list[str], list[ExercisePolicy]]:
    source = Path(path) if path else default_config_path()
    with source.open("rb") as handle:
        data = tomllib.load(handle)

    routines = [str(item) for item in data.get("routines", {}).get("names", [])]
    defaults = data.get("defaults", {})
    global_default = defaults.get("global", {})
    categories = defaults.get("categories", {})
    policies = []
    for name, values in data.get("exercises", {}).items():
        category = str(values.get("category", "global"))
        if category != "global" and category not in VALID_CATEGORIES:
            raise ValueError(f"exercise {name!r} has unknown category {category!r}")
        inherited = {**global_default, **categories.get(category, {}), **values}
        rep_min, rep_max = _rep_range(inherited, f"exercise {name!r}")
        increment = float(inherited["increment_lbs"])
        if increment <= 0:
            raise ValueError(f"exercise {name!r}.increment_lbs must be positive")
        sets = int(inherited["sets"])
        if sets <= 0:
            raise ValueError(f"exercise {name!r}.sets must be positive")
        policies.append(
            ExercisePolicy(
                name=name,
                aliases=tuple(str(alias) for alias in values.get("aliases", [])),
                sets=sets,
                rep_min=rep_min,
                rep_max=rep_max,
                increment=increment,
                category=category,
                large_increment=bool(inherited.get("large_increment", False)),
                starting_weight=(
                    float(values["starting_weight"]) if "starting_weight" in values else None
                ),
                increase_requires_confirmation=bool(
                    values.get("increase_requires_confirmation", False)
                ),
                display_name=str(values["display_name"]) if "display_name" in values else None,
            )
        )
    return routines, policies


def load_routine_policies(path: str | Path | None = None) -> list[RoutinePolicy]:
    source = Path(path) if path else default_config_path()
    with source.open("rb") as handle:
        data = tomllib.load(handle)
    return [
        RoutinePolicy(
            title=title,
            display_title=str(values.get("display_name", title)),
            exercises=tuple(str(name) for name in values.get("exercise_order", [])),
            warmup_exercises=tuple(str(name) for name in values.get("warmup_exercises", [])),
            aliases=tuple(str(alias) for alias in values.get("aliases", [])),
            warmup_set_counts=tuple(
                (str(name), int(count))
                for name, count in values.get("warmup_set_counts", {}).items()
            ),
        )
        for title, values in data.get("workouts", {}).items()
    ]


def resolve_routine(title: str, routines: list[RoutinePolicy]) -> RoutinePolicy | None:
    normalized = title.casefold()
    return next(
        (
            routine
            for routine in routines
            if normalized
            in {routine.title.casefold(), *(alias.casefold() for alias in routine.aliases)}
        ),
        None,
    )
