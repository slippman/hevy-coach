from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Action(str, Enum):
    ADD_REPS = "add reps"
    HOLD_WEIGHT = "hold weight"
    INCREASE_WEIGHT = "increase weight"
    REDUCE_WEIGHT = "reduce weight"
    REVIEW_TECHNIQUE = "review technique"
    INSUFFICIENT_DATA = "insufficient data"


@dataclass(frozen=True)
class SetRecord:
    routine: str
    started_at: datetime
    exercise: str
    set_index: int
    set_type: str
    weight: float | None
    reps: int | None
    rpe: float | None
    ended_at: datetime | None = None
    description: str = ""
    exercise_notes: str = ""
    distance: float | None = None
    duration_seconds: int | None = None

    @property
    def is_warmup(self) -> bool:
        value = self.set_type.strip().lower().replace("-", "_").replace(" ", "_")
        return value in {"warmup", "warm_up", "w"}


@dataclass(frozen=True)
class ExercisePolicy:
    name: str
    aliases: tuple[str, ...]
    sets: int
    rep_min: int
    rep_max: int
    increment: float
    starting_weight: float | None = None
    increase_requires_confirmation: bool = False
    display_name: str | None = None


@dataclass(frozen=True)
class Recommendation:
    exercise: str
    action: Action
    weight: float | None
    message: str
    evidence: str
    history_status: str = "established"


@dataclass(frozen=True)
class RoutinePolicy:
    title: str
    display_title: str
    exercises: tuple[str, ...]
    warmup_exercises: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
