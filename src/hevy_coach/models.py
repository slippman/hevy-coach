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


class DecisionReason(str, Enum):
    WEIGHT_UP = "WEIGHT_UP"
    WEIGHT_DOWN = "WEIGHT_DOWN"
    HOLD = "HOLD"
    ADD_REPS = "ADD_REPS"
    ADD_TIME = "ADD_TIME"
    CONFIRM = "CONFIRM"
    LIMITED_HISTORY = "LIMITED_HISTORY"


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
    category: str = "global"
    large_increment: bool = False
    starting_weight: float | None = None
    increase_requires_confirmation: bool = False
    display_name: str | None = None
    progression: str = "weighted_reps"
    duration_min_seconds: int | None = None
    duration_max_seconds: int | None = None
    duration_increment_seconds: int | None = None


@dataclass(frozen=True)
class Recommendation:
    exercise: str
    action: Action
    weight: float | None
    message: str
    evidence: str
    history_status: str = "established"


@dataclass(frozen=True)
class ExerciseDecision:
    recommendation: Recommendation
    target_weight: float | None
    reasoning_category: DecisionReason
    target_reps: tuple[int, ...] = ()
    target_durations: tuple[int, ...] = ()
    explanation: str = ""
    last_weight: float | None = None
    last_reps: tuple[int, ...] = ()
    last_durations: tuple[int, ...] = ()
    last_rpe: float | None = None
    rep_min: int | None = None
    rep_max: int | None = None


@dataclass(frozen=True)
class SupersetPolicy:
    exercises: tuple[str, ...]
    rest_min_seconds: int
    rest_max_seconds: int


@dataclass(frozen=True)
class RoutinePolicy:
    title: str
    display_title: str
    exercises: tuple[str, ...]
    warmup_exercises: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    warmup_set_counts: tuple[tuple[str, int], ...] = ()
    working_set_counts: tuple[tuple[str, int], ...] = ()
    supersets: tuple[SupersetPolicy, ...] = ()

    def warmup_set_count(self, exercise: str) -> int:
        return dict(self.warmup_set_counts).get(exercise, 0)

    def working_set_count(self, exercise: str, default: int) -> int:
        return dict(self.working_set_counts).get(exercise, default)
