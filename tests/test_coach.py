from datetime import UTC, datetime, timedelta
from pathlib import Path

from hevy_coach.coach import recommend_all, recommend_exercise, working_sets
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
        _set("Incline Bench Press (Dumbbell)", index, 35, 8, 7, first) for index in range(3)
    ] + [_set("Incline Bench Press (Dumbbell)", index, 35, 8, 7, second) for index in range(3)]

    recommendation = recommend_exercise(records, policy)

    assert recommendation.history_status == "established"
    assert recommendation.action is Action.INCREASE_WEIGHT
    assert recommendation.weight == 40


def test_bench_above_the_rep_ceiling_increases_from_the_latest_session() -> None:
    _, policies = load_config()
    policy = next(item for item in policies if item.name == "Bench Press (Dumbbell)")
    first = datetime(2024, 1, 1, tzinfo=UTC)
    second = first + timedelta(days=3)
    records = [_set("Dumbbell Bench Press", index, 45, 8, 8, first) for index in range(3)] + [
        _set("Dumbbell Bench Press", index, 45, 9, 8, second) for index in range(3)
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
