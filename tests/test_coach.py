from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hevy_coach.coach import next_session_target, recommend_all, recommend_exercise, working_sets
from hevy_coach.config import load_config, load_routine_policies, resolve_routine
from hevy_coach.gym_card import build_card, render_card, unknown_routine_exercises
from hevy_coach.models import Action, SetRecord
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
        if item.exercise == "Dumbbell Bench Press" and item.routine == "PF:Chest & Arms"
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


def test_routine_aliases_are_loaded() -> None:
    back_and_arms = next(
        routine for routine in load_routine_policies() if routine.title == "PF: Back & Arms"
    )

    assert back_and_arms.aliases == ("PF:Back & Arms", "PF:Back& Arms")
    for title in ("PF: Back & Arms", "PF:Back & Arms", "PF:Back& Arms"):
        assert resolve_routine(title, load_routine_policies()) == back_and_arms


def _set(
    exercise: str,
    index: int,
    weight: float,
    reps: int,
    rpe: float | None,
    started_at: datetime,
) -> SetRecord:
    return SetRecord(
        routine="PF:Back& Arms",
        started_at=started_at,
        exercise=exercise,
        set_index=index,
        set_type="normal",
        weight=weight,
        reps=reps,
        rpe=rpe,
    )


def test_first_session_back_and_arms_is_a_conservative_baseline() -> None:
    _, policies = load_config()
    routine = next(item for item in load_routine_policies() if item.title == "PF: Back & Arms")
    started_at = datetime(2024, 1, 1, tzinfo=UTC)
    records = [
        _set("Incline Bench Press (Dumbbell)", 0, 15, 8, None, started_at),
        _set("Incline Bench Press (Dumbbell)", 1, 35, 8, None, started_at),
        _set("Incline Bench Press (Dumbbell)", 2, 35, 8, None, started_at),
        _set("Incline Bench Press (Dumbbell)", 3, 35, 8, 7, started_at),
    ]
    policy = next(item for item in policies if item.name == "Incline Bench Press (Dumbbell)")

    recommendation = recommend_exercise(records, policy)
    title, items = build_card(routine, "PF:Back& Arms", records, policies)

    assert recommendation.action is Action.HOLD_WEIGHT
    assert recommendation.history_status == "limited"
    assert "trend" not in f"{recommendation.message} {recommendation.evidence}".casefold()
    assert items[0].history_status == "limited"
    assert render_card(title, items).endswith(
        "1     15    8\n2     35    8\n3     35    8\n4     35    8\n"
    )


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
    routine = next(item for item in load_routine_policies() if item.title == "PF: Back & Arms")
    started_at = datetime(2024, 1, 1, tzinfo=UTC)
    records = [_set("Temporary Cable Variation", 0, 17, 10, 7, started_at)]

    unknown = unknown_routine_exercises(routine, records, policies)

    assert unknown == ("Temporary Cable Variation",)
    assert "Temporary Cable Variation" not in routine.exercises


@pytest.mark.parametrize(
    ("exercise", "reps", "maximum"),
    [
        ("Cable Fly Crossovers", 13, 10),
        ("Lateral Raise (Dumbbell)", 13, 12),
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
    assert (raise_weight, raise_target) == (10, [12, 12, 12])


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
