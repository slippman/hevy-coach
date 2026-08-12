from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path

from .models import ExercisePolicy, RoutinePolicy


def default_config_path() -> Path:
    return Path(str(files("hevy_coach").joinpath("default_config.toml")))


def load_config(path: str | Path | None = None) -> tuple[list[str], list[ExercisePolicy]]:
    source = Path(path) if path else default_config_path()
    with source.open("rb") as handle:
        data = tomllib.load(handle)

    routines = [str(item) for item in data.get("routines", {}).get("names", [])]
    policies = []
    for name, values in data.get("exercises", {}).items():
        rep_range = values["rep_range"]
        policies.append(
            ExercisePolicy(
                name=name,
                aliases=tuple(str(alias) for alias in values.get("aliases", [])),
                sets=int(values["sets"]),
                rep_min=int(rep_range[0]),
                rep_max=int(rep_range[1]),
                increment=float(values["increment"]),
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
