from __future__ import annotations

from collections.abc import Iterable

from .models import Action, ExercisePolicy, Recommendation, SetRecord


def _matches(name: str, policy: ExercisePolicy) -> bool:
    candidates = (policy.name, *policy.aliases)
    return any(name.casefold() == candidate.casefold() for candidate in candidates)


def _fmt_weight(weight: float) -> str:
    value = float(weight)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _latest_working_sets(records: Iterable[SetRecord], policy: ExercisePolicy) -> list[SetRecord]:
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
    )


def _session_count(records: Iterable[SetRecord], policy: ExercisePolicy) -> int:
    return len({record.started_at for record in records if _matches(record.exercise, policy)})


def working_sets(records: list[SetRecord], prescribed_sets: int | None = None) -> list[SetRecord]:
    """Exclude marked warm-ups; for excess unmarked sets, retain the final prescribed sets.

    Hevy exports sometimes label all sets ``normal``. The latter rule is a transparent,
    conservative fallback that treats early ramp-up sets as warm-ups only when there are
    more logged normal sets than the configured prescription.
    """
    normal = [record for record in records if not record.is_warmup]
    if prescribed_sets is not None and len(normal) > prescribed_sets:
        return normal[-prescribed_sets:]
    return normal


def _evidence(sets: list[SetRecord], last_rpe: float | None) -> str:
    reps = "/".join("–" if item.reps is None else str(item.reps) for item in sets)
    rpe = "not logged" if last_rpe is None else f"{last_rpe:g}"
    return f"Working reps {reps}; last-set RPE {rpe}"


def recommend_exercise(records: Iterable[SetRecord], policy: ExercisePolicy) -> Recommendation:
    materialized = list(records)
    sets = _latest_working_sets(materialized, policy)
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
    evidence = _evidence(sets, last_rpe)

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
        target = "/".join(str(rep) for rep in reps[: policy.sets])
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
    exactly_at_ceiling = range_topped and all(rep == policy.rep_max for rep in reps[: policy.sets])
    if exactly_at_ceiling and policy.increase_requires_confirmation:
        return Recommendation(
            policy.name,
            Action.ADD_REPS,
            weight,
            f"Keep {label} lb; confirm {policy.sets}×{policy.rep_max} once more before increasing.",
            evidence,
        )
    if range_topped and (last_rpe is None or last_rpe <= 9.0):
        next_weight = weight + policy.increment
        return Recommendation(
            policy.name,
            Action.INCREASE_WEIGHT,
            next_weight,
            f"Increase one increment to {_fmt_weight(next_weight)} lb next time.",
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


def recommend_all(
    records: Iterable[SetRecord], policies: Iterable[ExercisePolicy]
) -> list[Recommendation]:
    materialized = list(records)
    return [recommend_exercise(materialized, policy) for policy in policies]


def summarize_workouts(records: Iterable[SetRecord]) -> tuple[int, str]:
    workouts = {(record.routine, record.started_at) for record in records}
    if not workouts:
        return 0, "none"
    latest = max(started_at for _, started_at in workouts)
    return len(workouts), latest.date().isoformat()
