from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hevy_coach.coach import (
    exercise_decision,
    next_duration_target,
    next_session_target,
    recommend_all,
    recommend_exercise,
    working_sets,
)
from hevy_coach.config import load_config, load_routine_policies, resolve_routine
from hevy_coach.gym_card import build_card, render_card, unknown_routine_exercises
from hevy_coach.models import Action, DecisionReason, SetRecord
from hevy_coach.parser import read_hevy_csv

FIXTURE = Path(__file__).parent / "fixtures" / "current_workouts.csv"


def test_configured_aliases_and_rules_produce_expected_directions() -> None:
    _, policies = load_config()
    items = {item.exercise: item for item in recommend_all(read_hevy_csv(FIXTURE), policies)}

    assert items["Bench Press (Dumbbell)"].action is Action.ADD_REPS
    assert items["Shoulder Press (Machine Plates)"].action is Action.HOLD_WEIGHT
    assert items["Triceps Pushdown"].action is Action.HOLD_WEIGHT
    assert items["Crunch (Machine)"].action is Action.INSUFFICIENT_DATA


def test_unmarked_sets_are_not_inferred_as_ramp_up_sets() -> None:
    records = [
        item
        for item in read_hevy_csv(FIXTURE)
        if item.exercise == "Dumbbell Bench Press" and item.routine == "Strength A"
    ]

    selected = working_sets(records, prescribed_sets=3)

    assert len(selected) == 4
    assert all(item.weight == 50 for item in selected)


def test_lighter_first_skullcrusher_set_remains_a_working_set() -> None:
    _, policies = load_config()
    policy = next(item for item in policies if item.name == "Skullcrusher (Dumbbell)")
    started_at = datetime(2024, 1, 1, tzinfo=UTC)
    records = [
        _set("Skullcrusher (Dumbbell)", 0, 20, 8, None, started_at),
        _set("Skullcrusher (Dumbbell)", 1, 25, 8, None, started_at),
        _set("Skullcrusher (Dumbbell)", 2, 25, 8, 7.5, started_at),
    ]

    selected = working_sets(records, policy.sets)
    recommendation = recommend_exercise(records, policy)

    assert [(item.weight, item.reps) for item in selected] == [(20, 8), (25, 8), (25, 8)]
    assert "Working reps 8/8/8" in recommendation.evidence
    assert recommendation.action is Action.HOLD_WEIGHT


def test_routine_names_ignore_spacing_and_punctuation_without_aliases() -> None:
    routine_b = next(
        routine for routine in load_routine_policies() if routine.title == "Strength B"
    )

    assert routine_b.aliases == ()
    for title in ("Strength B", "Strength-B", "StrengthB"):
        assert resolve_routine(title, load_routine_policies()) == routine_b


def test_bodyweight_routine_name_variants_and_modalities_are_configured() -> None:
    routines = load_routine_policies()
    routine = next(item for item in routines if item.title == "Bodyweight Circuit")
    _, policies = load_config()
    configured = {policy.name: policy for policy in policies}

    for title in (
        "Bodyweight Circuit",
        "BodyweightCircuit",
        "Bodyweight-Circuit",
        "Bodyweight Circuit",
    ):
        assert resolve_routine(title, routines) == routine
    assert routine.exercises == ("Push Up", "Pull Up", "Plank")
    assert routine.supersets[0].exercises == ("Push Up", "Pull Up")
    assert configured["Push Up"].progression == "bodyweight_reps"
    assert configured["Pull Up"].rep_min == 1
    assert configured["Plank"].progression == "duration"


def test_first_bodyweight_and_duration_session_repeat_actual_baseline() -> None:
    _, policies = load_config()
    configured = {policy.name: policy for policy in policies}
    started_at = datetime(2026, 1, 15, 10, tzinfo=UTC)
    push = [
        SetRecord(
            "BodyweightCircuit",
            started_at,
            "Push Up",
            index,
            "normal",
            None,
            10,
            7 if index == 2 else None,
        )
        for index in range(3)
    ]
    plank = [
        SetRecord(
            "BodyweightCircuit",
            started_at,
            "Plank",
            index,
            "normal",
            None,
            None,
            None,
            duration_seconds=seconds,
        )
        for index, seconds in enumerate((50, 55, 50))
    ]

    assert next_session_target(push, configured["Push Up"], "limited") == (
        None,
        [10, 10, 10],
    )
    assert next_duration_target(plank, configured["Plank"], "limited") == [50, 55, 50]


def test_first_bodyweight_and_timed_baselines_can_be_below_configured_minimum() -> None:
    _, policies = load_config()
    configured = {policy.name: policy for policy in policies}
    started_at = datetime(2026, 1, 15, 10, tzinfo=UTC)
    push = [
        SetRecord("BodyweightCircuit", started_at, "Push Up", index, "normal", None, 5, None)
        for index in range(3)
    ]
    plank = [
        SetRecord(
            "BodyweightCircuit",
            started_at,
            "Plank",
            index,
            "normal",
            None,
            None,
            None,
            duration_seconds=20,
        )
        for index in range(3)
    ]

    assert next_session_target(push, configured["Push Up"], "limited") == (None, [5, 5, 5])
    assert next_duration_target(plank, configured["Plank"], "limited") == [20, 20, 20]


def test_established_bodyweight_and_duration_progress_without_weight_logic() -> None:
    _, policies = load_config()
    configured = {policy.name: policy for policy in policies}
    started_at = datetime(2026, 1, 15, 10, tzinfo=UTC)
    pull = [
        SetRecord(
            "BodyweightCircuit",
            started_at,
            "Pull Up",
            index,
            "normal",
            None,
            reps,
            10 if index == 2 else None,
        )
        for index, reps in enumerate((3, 2, 2))
    ]
    plank = [
        SetRecord(
            "BodyweightCircuit",
            started_at,
            "Plank",
            index,
            "normal",
            None,
            None,
            None,
            duration_seconds=seconds,
        )
        for index, seconds in enumerate((50, 55, 50))
    ]

    assert next_session_target(pull, configured["Pull Up"], "established") == (
        None,
        [3, 2, 2],
    )
    assert next_duration_target(plank, configured["Plank"], "established") == [55, 60, 55]
    second_pull = [
        SetRecord(
            item.routine,
            item.started_at + timedelta(days=3),
            item.exercise,
            item.set_index,
            item.set_type,
            item.weight,
            item.reps,
            item.rpe,
        )
        for item in pull
    ]
    assert (
        recommend_exercise(pull + second_pull, configured["Pull Up"]).action is Action.HOLD_WEIGHT
    )


def _set(
    exercise: str,
    index: int,
    weight: float | None,
    reps: int,
    rpe: float | None,
    started_at: datetime,
) -> SetRecord:
    return SetRecord(
        routine="StrengthB",
        started_at=started_at,
        exercise=exercise,
        set_index=index,
        set_type="normal",
        weight=weight,
        reps=reps,
        rpe=rpe,
    )


def test_first_session_routine_is_a_conservative_baseline() -> None:
    _, policies = load_config()
    routine = next(item for item in load_routine_policies() if item.title == "Strength B")
    started_at = datetime(2024, 1, 1, tzinfo=UTC)
    records = [
        _set("Incline Bench Press (Dumbbell)", 0, 15, 8, None, started_at),
        _set("Incline Bench Press (Dumbbell)", 1, 35, 8, None, started_at),
        _set("Incline Bench Press (Dumbbell)", 2, 35, 8, None, started_at),
        _set("Incline Bench Press (Dumbbell)", 3, 35, 8, 7, started_at),
    ]
    policy = next(item for item in policies if item.name == "Incline Bench Press (Dumbbell)")

    recommendation = recommend_exercise(records, policy)
    title, items = build_card(routine, "StrengthB", records, policies)

    assert recommendation.action is Action.HOLD_WEIGHT
    assert recommendation.history_status == "limited"
    assert "trend" not in f"{recommendation.message} {recommendation.evidence}".casefold()
    assert items[0].history_status == "limited"
    rendered = render_card(title, items)
    assert "WORKOUT" in rendered
    assert "Incline DB Bench" in rendered
    assert "15×8" in rendered
    assert "35×8×3" in rendered
    assert "COACH'S SUMMARY" not in rendered


def test_second_session_uses_normal_progression_rules() -> None:
    _, policies = load_config()
    policy = next(item for item in policies if item.name == "Incline Bench Press (Dumbbell)")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    second = first + timedelta(days=3)
    records = [
        _set("Incline Bench Press (Dumbbell)", index, 35, 10, 7, first) for index in range(3)
    ] + [_set("Incline Bench Press (Dumbbell)", index, 35, 10, 7, second) for index in range(3)]

    recommendation = recommend_exercise(records, policy)

    assert recommendation.history_status == "established"
    assert recommendation.action is Action.INCREASE_WEIGHT
    assert recommendation.weight == 40


def test_bench_above_the_rep_ceiling_increases_from_the_latest_session() -> None:
    _, policies = load_config()
    policy = next(item for item in policies if item.name == "Bench Press (Dumbbell)")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    second = first + timedelta(days=3)
    records = [_set("Dumbbell Bench Press", index, 45, 10, 8, first) for index in range(3)] + [
        _set("Dumbbell Bench Press", index, 45, 10, 8, second) for index in range(3)
    ]

    recommendation = recommend_exercise(records, policy)

    assert recommendation.action is Action.INCREASE_WEIGHT
    assert recommendation.weight == 50
    assert recommendation.message == "Increase one increment to 50 lb next time."


def test_unknown_exercises_are_reported_without_changing_the_routine() -> None:
    _, policies = load_config()
    routine = next(item for item in load_routine_policies() if item.title == "Strength B")
    started_at = datetime(2024, 1, 1, tzinfo=UTC)
    records = [_set("Temporary Cable Variation", 0, 17, 10, 7, started_at)]

    unknown = unknown_routine_exercises(routine, records, policies)

    assert unknown == ("Temporary Cable Variation",)
    assert "Temporary Cable Variation" not in routine.exercises


@pytest.mark.parametrize(
    ("exercise", "reps", "maximum"),
    [
        ("Cable Fly Crossovers", 13, 10),
        ("Lateral Raise (Dumbbell)", 13, 10),
        ("Bench Press (Dumbbell)", 11, 10),
        ("Crunch (Machine)", 13, 12),
    ],
)
def test_card_targets_never_exceed_configured_rep_ceiling(
    exercise: str, reps: int, maximum: int
) -> None:
    _, policies = load_config()
    policy = next(item for item in policies if item.name == exercise)
    first = datetime(2024, 1, 1, tzinfo=UTC)
    second = first + timedelta(days=3)
    records = [_set(exercise, index, 10, reps, 8, first) for index in range(policy.sets)] + [
        _set(exercise, index, 10, reps, 8, second) for index in range(policy.sets)
    ]

    _, target = next_session_target(records[-policy.sets :], policy, "established", records)

    assert target == [policy.rep_min] * policy.sets
    assert max(target) <= maximum


def test_top_range_high_rpe_repeats_ceiling_and_large_increment_does_not_force_jump() -> None:
    _, policies = load_config()
    cable_fly = next(item for item in policies if item.name == "Cable Fly Crossovers")
    lateral_raise = next(item for item in policies if item.name == "Lateral Raise (Dumbbell)")
    started_at = datetime(2024, 1, 1, tzinfo=UTC)
    fly_sets = [_set(cable_fly.name, index, 10, 10, 9.5, started_at) for index in range(3)]
    raise_sets = [_set(lateral_raise.name, index, 10, 12, 8, started_at) for index in range(3)]

    fly_weight, fly_target = next_session_target(fly_sets, cable_fly, "established")
    raise_weight, raise_target = next_session_target(raise_sets, lateral_raise, "established")

    assert (fly_weight, fly_target) == (10, [10, 10, 10])
    assert (raise_weight, raise_target) == (10, [10, 10, 10])


def test_historical_reps_above_ceiling_are_clamped_in_decision_and_explanation() -> None:
    _, policies = load_config()
    policy = next(item for item in policies if item.name == "Lateral Raise (Dumbbell)")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    sessions = (
        (10, (12, 12, 12), 7),
        (12, (10, 10, 10), 8),
        (12, (12, 10, 10), 9),
    )
    records = [
        _set(
            policy.name,
            index,
            weight,
            rep,
            rpe,
            first + timedelta(days=session * 3),
        )
        for session, (weight, reps, rpe) in enumerate(sessions)
        for index, rep in enumerate(reps)
    ]

    decision = exercise_decision(records, policy)

    assert decision.target_weight == 12
    assert decision.target_reps == (10, 10, 10)
    assert max(decision.target_reps) <= policy.rep_max
    assert decision.reasoning_category is DecisionReason.HOLD
    assert "12 lb × 12/10/10" in decision.explanation
    assert "RPE 9" in decision.explanation


def test_duration_decision_never_exceeds_configured_maximum() -> None:
    _, policies = load_config()
    policy = next(item for item in policies if item.name == "Plank")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    records = [
        SetRecord(
            "Bodyweight Circuit",
            first + timedelta(days=session * 3),
            policy.name,
            index,
            "normal",
            None,
            None,
            8,
            duration_seconds=duration,
        )
        for session in range(2)
        for index, duration in enumerate((75, 65, 55))
    ]

    decision = exercise_decision(records, policy)

    assert decision.target_durations == (60, 60, 60)
    assert max(decision.target_durations) <= policy.duration_max_seconds


def test_bodyweight_baseline_never_exceeds_configured_rep_maximum() -> None:
    _, policies = load_config()
    policy = next(item for item in policies if item.name == "Push Up")
    started_at = datetime(2024, 1, 1, tzinfo=UTC)
    records = [_set(policy.name, index, None, 18, 8, started_at) for index in range(3)]

    decision = exercise_decision(records, policy)

    assert decision.target_weight is None
    assert decision.target_reps == (policy.rep_max,) * policy.sets


def test_configured_warmup_does_not_affect_progression_decision() -> None:
    _, policies = load_config()
    policy = next(item for item in policies if item.name == "Bench Press (Dumbbell)")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    records = [
        _set(policy.name, index, 25 if index == 0 else 45, 8 if index == 0 else 10, 8, date)
        for date in (first, first + timedelta(days=3))
        for index in range(4)
    ]

    decision = exercise_decision(records, policy, warmup_set_count=1)

    assert decision.recommendation.action is Action.INCREASE_WEIGHT
    assert decision.reasoning_category is DecisionReason.WEIGHT_UP
    assert decision.target_weight == 50
    assert decision.target_reps == (policy.rep_min,) * policy.sets
    assert "45 lb × 10 for all 3 sets" in decision.explanation
    assert "top of your 6–10 rep range" in decision.explanation
    assert "move up to 50 lb × 6 for all 3 sets" in decision.explanation


def test_high_rpe_uneven_sets_are_repeated_and_described_accurately() -> None:
    _, policies = load_config()
    policy = next(item for item in policies if item.name == "Bicep Curl (Dumbbell)")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    records = [
        _set(policy.name, index, 25, rep, 10, first + timedelta(days=session * 3))
        for session in range(2)
        for index, rep in enumerate((10, 10, 6))
    ]

    decision = exercise_decision(records, policy)

    assert decision.reasoning_category is DecisionReason.HOLD
    assert decision.target_reps == (10, 10, 6)
    assert "25 lb × 10/10/6 at RPE 10" in decision.explanation
    assert "repeat 25 lb × 10/10/6" in decision.explanation


def _ceiling_session(
    policy_name: str, started_at: datetime, rpe: float, reps: int
) -> list[SetRecord]:
    return [_set(policy_name, index, 10, reps, rpe, started_at) for index in range(3)]


def test_large_increment_requires_two_consecutive_successful_ceiling_sessions() -> None:
    _, policies = load_config()
    lateral_raise = next(item for item in policies if item.name == "Lateral Raise (Dumbbell)")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    previous_miss = _ceiling_session(lateral_raise.name, first, 8, lateral_raise.rep_max - 1)
    first_success = _ceiling_session(
        lateral_raise.name, first + timedelta(days=3), 8, lateral_raise.rep_max
    )
    all_records = previous_miss + first_success

    weight, target = next_session_target(first_success, lateral_raise, "established", all_records)
    recommendation = recommend_exercise(all_records, lateral_raise)

    assert (weight, target) == (10, [lateral_raise.rep_max] * 3)
    assert recommendation.action is Action.HOLD_WEIGHT
    assert "once more" in recommendation.message
    assert max(target) == lateral_raise.rep_max

    decision = exercise_decision(all_records, lateral_raise)
    assert decision.reasoning_category is DecisionReason.CONFIRM
    assert "next available weight is a large jump" in decision.explanation
    assert "successfully once more" in decision.explanation

    second_success = _ceiling_session(
        lateral_raise.name, first + timedelta(days=6), 8, lateral_raise.rep_max
    )
    all_records += second_success
    weight, target = next_session_target(second_success, lateral_raise, "established", all_records)
    recommendation = recommend_exercise(all_records, lateral_raise)

    assert (weight, target) == (15, [lateral_raise.rep_min] * 3)
    assert recommendation.action is Action.INCREASE_WEIGHT
    assert recommendation.weight == 15


def test_large_increment_high_rpe_or_missed_ceiling_resets_confirmation_streak() -> None:
    _, policies = load_config()
    lateral_raise = next(item for item in policies if item.name == "Lateral Raise (Dumbbell)")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    success = _ceiling_session(lateral_raise.name, first, 8, lateral_raise.rep_max)
    high_rpe = _ceiling_session(
        lateral_raise.name, first + timedelta(days=3), 9, lateral_raise.rep_max
    )
    after_high_rpe = _ceiling_session(
        lateral_raise.name, first + timedelta(days=6), 8, lateral_raise.rep_max
    )
    records = success + high_rpe + after_high_rpe

    weight, target = next_session_target(after_high_rpe, lateral_raise, "established", records)

    assert (weight, target) == (10, [lateral_raise.rep_max] * 3)

    missed_ceiling = _ceiling_session(
        lateral_raise.name, first + timedelta(days=9), 8, lateral_raise.rep_max - 2
    )
    after_miss = _ceiling_session(
        lateral_raise.name, first + timedelta(days=12), 8, lateral_raise.rep_max
    )
    records += missed_ceiling + after_miss
    weight, target = next_session_target(after_miss, lateral_raise, "established", records)

    assert (weight, target) == (10, [lateral_raise.rep_max] * 3)
    assert max(target) <= lateral_raise.rep_max


def test_skipping_an_exercise_in_a_partial_workout_does_not_reset_its_progression() -> None:
    _, policies = load_config()
    lateral_raise = next(item for item in policies if item.name == "Lateral Raise (Dumbbell)")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    first_success = _ceiling_session(lateral_raise.name, first, 8, lateral_raise.rep_max)
    unrelated_partial = [_set("Dumbbell Bench Press", 0, 45, 9, 8, first + timedelta(days=1))]
    second_success = _ceiling_session(
        lateral_raise.name, first + timedelta(days=3), 8, lateral_raise.rep_max
    )
    records = first_success + unrelated_partial + second_success

    weight, target = next_session_target(second_success, lateral_raise, "established", records)

    assert (weight, target) == (15, [lateral_raise.rep_min] * 3)


def test_normal_increment_exercise_progresses_after_one_successful_ceiling_session() -> None:
    _, policies = load_config()
    cable_fly = next(item for item in policies if item.name == "Cable Fly Crossovers")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    latest = _ceiling_session(cable_fly.name, first, 8, cable_fly.rep_max)

    weight, target = next_session_target(latest, cable_fly, "established", latest)

    assert (weight, target) == (15, [cable_fly.rep_min] * 3)


def test_config_resolution_and_validation(tmp_path: Path) -> None:
    config = tmp_path / "coach.toml"
    config.write_text(
        """[defaults.global]
sets = 3
min_reps = 5
max_reps = 15
increment_lbs = 2
[defaults.categories.isolation]
min_reps = 8
max_reps = 10
[exercises.\"Inherited\"]
category = \"isolation\"
[exercises.\"Overridden\"]
category = \"isolation\"
min_reps = 9
max_reps = 9
increment_lbs = 3
""",
        encoding="utf-8",
    )

    _, policies = load_config(config)
    inherited, overridden = policies

    assert (inherited.rep_min, inherited.rep_max, inherited.increment) == (8, 10, 2)
    assert (overridden.rep_min, overridden.rep_max, overridden.increment) == (9, 9, 3)

    config.write_text(
        """[defaults.global]
sets = 3
min_reps = 10
max_reps = 8
increment_lbs = 0
[exercises.\"Broken\"]
category = \"unknown\"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown category"):
        load_config(config)

    config.write_text(
        """[defaults.global]
sets = 3
min_reps = 10
max_reps = 8
increment_lbs = 5
[exercises.\"Broken\"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="min_reps greater"):
        load_config(config)

    config.write_text(
        """[defaults.global]
sets = 3
min_reps = 8
max_reps = 10
increment_lbs = 0
[exercises.\"Broken\"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="increment_lbs must be positive"):
        load_config(config)
