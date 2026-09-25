from __future__ import annotations

from collections.abc import Iterable

from .models import (
    Action,
    DecisionReason,
    ExerciseDecision,
    ExercisePolicy,
    Recommendation,
    SetRecord,
)
from .time_utils import local_date


def _matches(name: str, policy: ExercisePolicy) -> bool:
    candidates = (policy.name, *policy.aliases)
    return any(name.casefold() == candidate.casefold() for candidate in candidates)


def _fmt_weight(weight: float) -> str:
    value = float(weight)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _latest_working_sets(
    records: Iterable[SetRecord], policy: ExercisePolicy, warmup_set_count: int = 0
) -> list[SetRecord]:
    matches = [record for record in records if _matches(record.exercise, policy)]
    if not matches:
        return []
    latest = max(record.started_at for record in matches)
    return working_sets(
        sorted(
            (record for record in matches if record.started_at == latest),
            key=lambda record: record.set_index,
        ),
        policy.sets,
        warmup_set_count,
    )


def _session_count(records: Iterable[SetRecord], policy: ExercisePolicy) -> int:
    return len({record.started_at for record in records if _matches(record.exercise, policy)})


def _exercise_sessions(
    records: Iterable[SetRecord], policy: ExercisePolicy, warmup_set_count: int = 0
) -> list[list[SetRecord]]:
    """Return the exercise's working sets grouped chronologically by session."""
    grouped: dict[object, list[SetRecord]] = {}
    for record in records:
        if _matches(record.exercise, policy):
            grouped.setdefault(record.started_at, []).append(record)
    return [
        working_sets(
            sorted(session, key=lambda record: record.set_index), policy.sets, warmup_set_count
        )
        for _, session in sorted(grouped.items())
    ]


def working_sets(
    records: list[SetRecord], prescribed_sets: int | None = None, warmup_set_count: int = 0
) -> list[SetRecord]:
    """Exclude only explicit or configured warm-ups; never infer them from load changes."""
    del prescribed_sets
    explicit_warmups = [record for record in records if record.is_warmup]
    normal = [record for record in records if not record.is_warmup]
    configured_unmarked_warmups = max(0, warmup_set_count - len(explicit_warmups))
    return normal[configured_unmarked_warmups:]


def _successful_ceiling_session(sets: list[SetRecord], policy: ExercisePolicy) -> bool:
    """A large jump needs a complete, low-RPE session at the rep ceiling."""
    reps = [item.reps for item in sets[: policy.sets] if item.reps is not None]
    last_rpe = next((item.rpe for item in reversed(sets) if item.rpe is not None), None)
    return (
        len(reps) >= policy.sets
        and all(rep >= policy.rep_max for rep in reps)
        and last_rpe is not None
        and last_rpe <= 8.5
    )


def large_increment_confirmation_streak(
    records: Iterable[SetRecord], policy: ExercisePolicy, warmup_set_count: int = 0
) -> int:
    """Count consecutive successful ceiling sessions ending with the latest one."""
    streak = 0
    for session in reversed(_exercise_sessions(records, policy, warmup_set_count)):
        if not _successful_ceiling_session(session, policy):
            break
        streak += 1
    return streak


def _evidence(sets: list[SetRecord], last_rpe: float | None) -> str:
    reps = "/".join("–" if item.reps is None else str(item.reps) for item in sets)
    rpe = "not logged" if last_rpe is None else f"{last_rpe:g}"
    return f"Working reps {reps}; last-set RPE {rpe}"


def _duration_evidence(sets: list[SetRecord], last_rpe: float | None) -> str:
    durations = "/".join(
        "–" if item.duration_seconds is None else str(item.duration_seconds) for item in sets
    )
    rpe = "not logged" if last_rpe is None else f"{last_rpe:g}"
    return f"Working duration {durations} seconds; last-set RPE {rpe}"


def next_session_target(
    sets: list[SetRecord],
    policy: ExercisePolicy,
    history_status: str,
    history_records: Iterable[SetRecord] | None = None,
    warmup_set_count: int = 0,
) -> tuple[float | None, list[int]]:
    """Return a ceiling-safe next load and rep targets from resolved policy."""
    weight = next((item.weight for item in reversed(sets) if item.weight is not None), None)
    logged = [item.reps for item in sets if item.reps is not None]
    if not logged:
        return weight, [policy.rep_min] * policy.sets
    capped_reps = [min(policy.rep_max, rep) for rep in logged[: policy.sets]]
    if history_status == "limited":
        return (None if policy.progression == "bodyweight_reps" else weight), capped_reps
    last_rpe = next((item.rpe for item in reversed(sets) if item.rpe is not None), None)
    if policy.progression == "bodyweight_reps" and last_rpe is not None and last_rpe >= 9.5:
        return None, capped_reps
    reps = [max(policy.rep_min, rep) for rep in capped_reps]
    if policy.progression == "bodyweight_reps":
        if len(reps) >= policy.sets and all(rep >= policy.rep_max for rep in reps):
            return None, [policy.rep_max] * policy.sets
        if len(set(reps)) == 1:
            return None, [min(policy.rep_max, reps[0] + 1)] * len(reps)
        lowest = min(range(len(reps)), key=reps.__getitem__)
        reps[lowest] = min(policy.rep_max, reps[lowest] + 1)
        return None, reps
    if last_rpe is not None and last_rpe >= 9.5:
        if min(logged) <= policy.rep_min - 3:
            return (max(0.0, weight - policy.increment) if weight is not None else None), [
                policy.rep_min
            ] * policy.sets
        return weight, [min(policy.rep_max, rep) for rep in logged[: policy.sets]]
    at_ceiling = len(reps) >= policy.sets and all(rep >= policy.rep_max for rep in reps)
    if at_ceiling:
        if (last_rpe is None or last_rpe <= 8.5) and not policy.large_increment:
            return (weight + policy.increment if weight is not None else None), [
                policy.rep_min
            ] * policy.sets
        if policy.large_increment and _successful_ceiling_session(sets, policy):
            history = list(history_records) if history_records is not None else sets
            if large_increment_confirmation_streak(history, policy, warmup_set_count) >= 2:
                return (weight + policy.increment if weight is not None else None), [
                    policy.rep_min
                ] * policy.sets
        return weight, [policy.rep_max] * policy.sets
    if len(set(reps)) == 1:
        return weight, [min(policy.rep_max, reps[0] + 1)] * len(reps)
    lowest = min(range(len(reps)), key=reps.__getitem__)
    reps[lowest] = min(policy.rep_max, reps[lowest] + 1)
    return weight, reps


def next_duration_target(
    sets: list[SetRecord], policy: ExercisePolicy, history_status: str
) -> list[int]:
    """Return duration targets without treating timed work as weighted or rep-based."""
    logged = [item.duration_seconds for item in sets if item.duration_seconds is not None]
    minimum = policy.duration_min_seconds or 1
    maximum = policy.duration_max_seconds or minimum
    increment = policy.duration_increment_seconds or 1
    if not logged:
        return [minimum] * policy.sets
    capped_durations = [min(maximum, value) for value in logged[: policy.sets]]
    if history_status == "limited":
        return capped_durations
    last_rpe = next((item.rpe for item in reversed(sets) if item.rpe is not None), None)
    if last_rpe is not None and last_rpe >= 9.5:
        return capped_durations
    durations = [max(minimum, value) for value in capped_durations]
    return [min(maximum, value + increment) for value in durations]


def recommend_exercise(
    records: Iterable[SetRecord], policy: ExercisePolicy, warmup_set_count: int = 0
) -> Recommendation:
    materialized = list(records)
    sets = _latest_working_sets(materialized, policy, warmup_set_count)
    history_status = "limited" if _session_count(materialized, policy) <= 1 else "established"
    if not sets:
        start = policy.starting_weight
        if start is None:
            return Recommendation(
                policy.name,
                Action.INSUFFICIENT_DATA,
                None,
                f"No recent working sets found; choose a conservative starting weight for "
                f"{policy.sets}×{policy.rep_min}.",
                "No matching exercise history",
                history_status,
            )
        weight = _fmt_weight(start)
        return Recommendation(
            policy.name,
            Action.INSUFFICIENT_DATA,
            start,
            f"Start at {weight} lb for {policy.sets}×{policy.rep_min}; increase only after all "
            "sets are clean.",
            "No matching exercise history",
            history_status,
        )

    weighted = [item.weight for item in sets if item.weight is not None]
    weight = weighted[-1] if weighted else None
    reps = [item.reps for item in sets if item.reps is not None]
    rpes = [item.rpe for item in sets if item.rpe is not None]
    last_rpe = rpes[-1] if rpes else None
    evidence = (
        _duration_evidence(sets, last_rpe)
        if policy.progression == "duration"
        else _evidence(sets, last_rpe)
    )

    if policy.progression == "duration":
        durations = next_duration_target(sets, policy, history_status)
        target = "/".join(str(value) for value in durations)
        logged_durations = [
            min(policy.duration_max_seconds or value, value)
            for value in (item.duration_seconds for item in sets)
            if value is not None
        ][: policy.sets]
        should_hold = history_status == "limited" or durations == logged_durations
        return Recommendation(
            policy.name,
            Action.HOLD_WEIGHT if should_hold else Action.ADD_TIME,
            None,
            f"Hold for {target} seconds.",
            evidence,
            history_status,
        )

    if policy.progression == "bodyweight_reps":
        _, targets = next_session_target(
            sets, policy, history_status, materialized, warmup_set_count
        )
        target = "/".join(str(rep) for rep in targets)
        at_ceiling = len(reps) >= policy.sets and all(
            rep >= policy.rep_max for rep in reps[: policy.sets]
        )
        should_hold = (
            history_status == "limited" or (last_rpe is not None and last_rpe >= 9.5) or at_ceiling
        )
        return Recommendation(
            policy.name,
            Action.HOLD_WEIGHT if should_hold else Action.ADD_REPS,
            None,
            f"Bodyweight · {target}.",
            evidence,
            history_status,
        )

    if weight is None or not reps:
        return Recommendation(
            policy.name,
            Action.HOLD_WEIGHT,
            weight,
            "Hold the load until a complete set of reps is logged.",
            evidence,
        )

    label = _fmt_weight(weight)
    if history_status == "limited":
        _, target_reps = next_session_target(sets, policy, history_status)
        target = "/".join(str(rep) for rep in target_reps)
        return Recommendation(
            policy.name,
            Action.HOLD_WEIGHT,
            weight,
            f"Keep {label} lb; repeat {target} as a baseline.",
            evidence,
            history_status,
        )

    far_below_range = min(reps) <= policy.rep_min - 3
    if last_rpe is not None and last_rpe >= 9.5 and far_below_range:
        next_weight = max(0.0, weight - policy.increment)
        return Recommendation(
            policy.name,
            Action.REDUCE_WEIGHT,
            next_weight,
            f"Reduce to {_fmt_weight(next_weight)} lb; regain {policy.sets}×{policy.rep_min} "
            "with clean reps.",
            evidence,
        )

    if last_rpe is not None and last_rpe >= 9.5:
        return Recommendation(
            policy.name,
            Action.HOLD_WEIGHT,
            weight,
            f"Keep {label} lb; RPE {last_rpe:g} means don’t increase yet.",
            evidence,
        )

    completed_sets = len(reps) >= policy.sets
    range_topped = completed_sets and all(rep >= policy.rep_max for rep in reps[: policy.sets])
    if range_topped and policy.increase_requires_confirmation:
        return Recommendation(
            policy.name,
            Action.HOLD_WEIGHT,
            weight,
            f"Keep {label} lb; confirm {policy.sets}×{policy.rep_max} once more before increasing.",
            evidence,
        )
    if range_topped and policy.large_increment:
        successful = _successful_ceiling_session(sets, policy)
        confirmations = large_increment_confirmation_streak(materialized, policy, warmup_set_count)
        if successful and confirmations >= 2:
            next_weight = weight + policy.increment
            return Recommendation(
                policy.name,
                Action.INCREASE_WEIGHT,
                next_weight,
                f"Increase one increment to {_fmt_weight(next_weight)} lb next time.",
                evidence,
            )
        if successful:
            message = (
                f"Keep {label} lb; repeat {policy.sets}×{policy.rep_max} once more before "
                "taking the large weight jump."
            )
        else:
            message = f"Keep {label} lb; repeat {policy.sets}×{policy.rep_max} before increasing."
        return Recommendation(policy.name, Action.HOLD_WEIGHT, weight, message, evidence)
    if range_topped and (last_rpe is None or last_rpe <= 8.5) and not policy.large_increment:
        next_weight = weight + policy.increment
        return Recommendation(
            policy.name,
            Action.INCREASE_WEIGHT,
            next_weight,
            f"Increase one increment to {_fmt_weight(next_weight)} lb next time.",
            evidence,
        )
    if range_topped:
        return Recommendation(
            policy.name,
            Action.HOLD_WEIGHT,
            weight,
            f"Keep {label} lb; repeat {policy.sets}×{policy.rep_max} before increasing.",
            evidence,
        )

    if min(reps) < policy.rep_min:
        return Recommendation(
            policy.name,
            Action.ADD_REPS,
            weight,
            f"Keep {label} lb; improve the final set above {reps[-1]} before adding weight.",
            evidence,
        )

    if policy.name == "DB Curl" and reps[-1] <= policy.rep_min:
        return Recommendation(
            policy.name,
            Action.ADD_REPS,
            weight,
            f"Keep {label} lb; aim to improve the final set above {reps[-1]} before adding weight.",
            evidence,
        )

    next_reps = reps[: policy.sets]
    if next_reps and len(set(next_reps)) > 1:
        lowest = min(range(len(next_reps)), key=next_reps.__getitem__)
        next_reps[lowest] = min(policy.rep_max, next_reps[lowest] + 1)
        target = "/".join(str(rep) for rep in next_reps)
        message = f"Keep {label} lb; target {target} before increasing."
    elif policy.name == "DB Bench" and reps[0] == policy.rep_min:
        message = (
            f"Keep {label} lb; aim for {policy.sets}×{policy.rep_min} again or begin pushing "
            f"toward {policy.sets}×{policy.rep_min + 1}."
        )
    else:
        message = f"Keep {label} lb; build toward {policy.sets}×{policy.rep_max}."
    return Recommendation(policy.name, Action.ADD_REPS, weight, message, evidence)


def _last_performance(sets: list[SetRecord], policy: ExercisePolicy) -> str:
    last_rpe = next((item.rpe for item in reversed(sets) if item.rpe is not None), None)
    rpe = "" if last_rpe is None else f" at RPE {last_rpe:g}"
    if policy.progression == "duration":
        values = [item.duration_seconds for item in sets if item.duration_seconds is not None]
        if len(set(values)) == 1:
            return f"{values[0]} seconds for all {len(values)} sets{rpe}"
        return f"{'/'.join(str(value) for value in values)} seconds{rpe}"
    reps = [item.reps for item in sets if item.reps is not None]
    if not reps:
        return "an incomplete set entry"
    uniform = len(set(reps)) == 1
    rep_text = f"{reps[0]} for all {len(reps)} sets" if uniform else "/".join(map(str, reps))
    if policy.progression == "bodyweight_reps":
        return f"{rep_text} reps{rpe}"
    weighted = [(item.weight, item.reps) for item in sets if item.reps is not None]
    weights = [weight for weight, _ in weighted if weight is not None]
    if weights and len(weights) == len(weighted) and len(set(weights)) == 1:
        return f"{_fmt_weight(weights[0])} lb × {rep_text}{rpe}"
    details = ", ".join(
        f"{_fmt_weight(weight)} lb × {reps}" if weight is not None else f"{reps} reps"
        for weight, reps in weighted
    )
    return f"{details}{rpe}"


def _target_description(
    weight: float | None,
    reps: tuple[int, ...],
    durations: tuple[int, ...],
    policy: ExercisePolicy,
) -> str:
    if durations:
        values = (
            f"{durations[0]} seconds for all {len(durations)} sets"
            if len(set(durations)) == 1
            else f"{'/'.join(map(str, durations))} seconds"
        )
        return values
    uniform = reps and len(set(reps)) == 1
    rep_values = f"{reps[0]} for all {len(reps)} sets" if uniform else "/".join(map(str, reps))
    if policy.progression == "bodyweight_reps" or weight is None:
        return f"{rep_values} reps"
    return f"{_fmt_weight(weight)} lb × {rep_values}"


def exercise_decision(
    records: Iterable[SetRecord], policy: ExercisePolicy, warmup_set_count: int = 0
) -> ExerciseDecision:
    """Return targets and a concise explanation from one recommendation decision."""
    materialized = list(records)
    sets = _latest_working_sets(materialized, policy, warmup_set_count)
    history_status = "limited" if _session_count(materialized, policy) <= 1 else "established"
    recommendation = recommend_exercise(materialized, policy, warmup_set_count)
    if policy.progression == "duration":
        durations = tuple(next_duration_target(sets, policy, history_status))
        weight = None
        reps: tuple[int, ...] = ()
    else:
        weight, target_reps = next_session_target(
            sets, policy, history_status, materialized, warmup_set_count
        )
        reps = tuple(min(policy.rep_max, rep) for rep in target_reps)
        durations = ()

    last = _last_performance(sets, policy) if sets else "no prior working sets"
    last_sentence = f"Last time you completed {last}."
    target = _target_description(weight, reps, durations, policy)
    if history_status == "limited":
        reason = DecisionReason.LIMITED_HISTORY
        explanation = (
            f"Baseline repeated: {last_sentence} With only one session available, repeat {target} "
            "before progressing."
        )
    elif policy.large_increment and "large weight jump" in recommendation.message:
        reason = DecisionReason.CONFIRM
        explanation = (
            f"Large-jump confirmation: {last_sentence} You reached the top of your "
            f"{policy.rep_min}–{policy.rep_max} rep range, but the next available weight is a "
            f"large jump. Repeat {target} successfully once more before increasing the weight."
        )
    elif recommendation.action is Action.INCREASE_WEIGHT:
        reason = DecisionReason.WEIGHT_UP
        explanation = (
            f"Weight increased: {last_sentence} You reached the top of your "
            f"{policy.rep_min}–{policy.rep_max} rep range with room left, so move up to {target}."
        )
    elif recommendation.action is Action.REDUCE_WEIGHT:
        reason = DecisionReason.WEIGHT_DOWN
        explanation = (
            f"Weight reduced: {last_sentence} Reduce the load to {target} so you can rebuild "
            "the target reps with clean form."
        )
    elif recommendation.action in {Action.ADD_REPS, Action.ADD_TIME}:
        adding_time = recommendation.action is Action.ADD_TIME
        reason = DecisionReason.ADD_TIME if adding_time else DecisionReason.ADD_REPS
        label = "Adding time" if adding_time else "Adding reps"
        explanation = (
            f"{label}: {last_sentence} Keep the same resistance and aim for {target}; you are "
            f"still building toward the top of your {policy.rep_min}–{policy.rep_max} rep range."
            if not adding_time
            else f"{label}: {last_sentence} Keep the same exercise and aim for {target}."
        )
    else:
        reason = DecisionReason.HOLD
        last_rpe = next((item.rpe for item in reversed(sets) if item.rpe is not None), None)
        effort = (
            f" That effort is too high to progress, so repeat {target} until it feels more "
            "comfortable."
            if last_rpe is not None and last_rpe >= 9
            else f" Repeat {target} before progressing."
        )
        explanation = f"Holding steady: {last_sentence}{effort}"
    return ExerciseDecision(
        recommendation=recommendation,
        target_weight=weight,
        reasoning_category=reason,
        target_reps=reps,
        target_durations=durations,
        explanation=explanation,
        last_weight=next((item.weight for item in reversed(sets) if item.weight is not None), None),
        last_reps=tuple(item.reps for item in sets if item.reps is not None),
        last_durations=tuple(
            item.duration_seconds for item in sets if item.duration_seconds is not None
        ),
        last_rpe=next((item.rpe for item in reversed(sets) if item.rpe is not None), None),
        rep_min=policy.rep_min,
        rep_max=policy.rep_max,
    )


def recommend_all(
    records: Iterable[SetRecord],
    policies: Iterable[ExercisePolicy],
    warmup_set_counts: dict[str, int] | None = None,
) -> list[Recommendation]:
    materialized = list(records)
    counts = warmup_set_counts or {}
    return [
        recommend_exercise(materialized, policy, counts.get(policy.name, 0)) for policy in policies
    ]


def summarize_workouts(records: Iterable[SetRecord]) -> tuple[int, str]:
    workouts = {(record.routine, record.started_at) for record in records}
    if not workouts:
        return 0, "none"
    latest = max(started_at for _, started_at in workouts)
    return len(workouts), local_date(latest).isoformat()
