import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from hevy_coach.cli import main
from hevy_coach.gym_card import build_card
from hevy_coach.importer import import_csv
from hevy_coach.models import ExercisePolicy, RoutinePolicy, SetRecord
from hevy_coach.storage import database


def _seed_shared_history(tmp_path: Path, rpe: float = 8) -> Path:
    (tmp_path / "config.toml").write_text(
        """[defaults.global]
sets = 3
min_reps = 8
max_reps = 12
increment_lbs = 10
[routines]
names = ["Strength A", "Strength B"]
[workouts."Strength A"]
exercise_order = ["Press", "Core Machine"]
working_set_counts = { "Core Machine" = 4 }
[workouts."Strength B"]
exercise_order = ["Core Machine", "Row"]
warmup_set_counts = { "Core Machine" = 1 }
[exercises."Press"]
[exercises."Core Machine"]
aliases = ["Machine Core"]
[exercises."Row"]
""",
        encoding="utf-8",
    )
    rows = ["title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe"]
    for title, when, exercise, weight, reps, count, effort in (
        ("Strength A", "2026-01-01", "Core Machine", 60, 12, 4, 8),
        ("Strength A", "2026-01-01", "Press", 30, 9, 3, 8),
        ("Strength-B", "2026-01-03", "Machine Core", 75, 8, 4, rpe),
        ("Strength-B", "2026-01-03", "Row", 40, 9, 3, 8),
        ("Strength A", "2026-01-05", "Press", 30, 9, 3, 8),
    ):
        offset = 1 if exercise == "Machine Core" else 0
        if offset:
            rows.append(f"{title},{when} 10:00:00,{exercise},0,normal,20,8,5")
        for index in range(count):
            rows.append(
                f"{title},{when} 10:00:00,{exercise},{index + offset},normal,"
                f"{weight},{reps},{effort if index == count - 1 else ''}"
            )
    source = tmp_path / "synthetic.csv"
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, tmp_path / "imports")
    return db


@pytest.mark.parametrize(("rpe", "target_reps"), [(8, 9), (10, 8)])
def test_card_uses_latest_shared_performance_and_selected_routine_set_count(
    tmp_path: Path, rpe: float, target_reps: int
) -> None:
    db = _seed_shared_history(tmp_path, rpe)
    result = CliRunner().invoke(
        main,
        ["gym-card", "--workout", "Strength A", "--json", "--db", str(db)],
        env={"HEVY_API_KEY": ""},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [item["exercise"] for item in payload["exercises"]] == ["Press", "Core Machine"]
    core = payload["exercises"][1]
    assert core["weight_lbs"] == 75
    assert core["last_performance"]["rpe"] == rpe
    assert core["last_performance"]["reps"] == [8] * 4
    assert core["history_status"] == "established"
    assert len(core["sets"]) == 5  # source routine's warm-up plus four target working sets
    assert core["sets"][0]["weight_lbs"] == 20
    assert [item["reps"] for item in core["sets"][1:]] == [target_reps] * 4
    assert payload["source_workout_date"] == "2026-01-03"


def test_report_shares_history_without_leaking_other_routine_exercises(tmp_path: Path) -> None:
    db = _seed_shared_history(tmp_path)
    result = CliRunner().invoke(main, ["report", "--latest", "--json", "--db", str(db)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [item["exercise"] for item in payload["exercises"]] == ["Press"]
    assert [item["exercise"] for item in payload["recommendations"]] == ["Press", "Core Machine"]
    core = payload["recommendations"][1]
    assert core["weight"] == 75
    assert "Working reps 8/8/8/8" in core["evidence"]


def test_other_routine_keeps_its_own_working_set_count(tmp_path: Path) -> None:
    db = _seed_shared_history(tmp_path)
    result = CliRunner().invoke(
        main,
        ["gym-card", "--workout", "Strength-B", "--json", "--db", str(db)],
        env={"HEVY_API_KEY": ""},
    )
    assert result.exit_code == 0, result.output
    core = json.loads(result.stdout)["exercises"][0]
    assert len(core["sets"]) == 4  # one source warm-up plus three configured working sets
    assert [item["weight_lbs"] for item in core["sets"][1:]] == [75] * 3


def test_destination_warmup_count_does_not_discard_source_working_sets(tmp_path: Path) -> None:
    db = _seed_shared_history(tmp_path)
    source = tmp_path / "newer-synthetic.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        + "".join(
            f"Strength A,2026-01-06 10:00:00,Core Machine,{index},normal,85,10,10\n"
            for index in range(3)
        ),
        encoding="utf-8",
    )
    with database(db) as connection:
        import_csv(connection, source, tmp_path / "imports")
    result = CliRunner().invoke(
        main,
        ["gym-card", "--workout", "Strength-B", "--json", "--db", str(db)],
        env={"HEVY_API_KEY": ""},
    )
    assert result.exit_code == 0, result.output
    core = json.loads(result.stdout)["exercises"][0]
    assert core["warmup"] is None
    assert core["last_performance"]["reps"] == [10, 10, 10]
    assert len(core["sets"]) == 3
    assert [item["weight_lbs"] for item in core["sets"]] == [85] * 3


@pytest.mark.parametrize("progression", ["weighted_reps", "bodyweight_reps", "duration"])
@pytest.mark.parametrize("logged_count", [2, 3])
def test_complete_shorter_source_adapts_to_destination_set_count(progression, logged_count):
    policy = ExercisePolicy(
        "Movement",
        (),
        3,
        8,
        12,
        5,
        progression=progression,
        duration_min_seconds=30,
        duration_max_seconds=60,
        duration_increment_seconds=5,
    )
    destination = RoutinePolicy(
        "Strength A", "Strength A", (policy.name,), working_set_counts=((policy.name, 4),)
    )
    source = RoutinePolicy("Strength B", "Strength B", (policy.name,))
    first = datetime(2026, 1, 1, tzinfo=UTC)
    history = [
        SetRecord(
            title,
            first + timedelta(days=day),
            policy.name,
            index,
            "normal",
            40 if progression == "weighted_reps" else None,
            None if progression == "duration" else 8,
            8,
            duration_seconds=40 if progression == "duration" else None,
        )
        for title, day, count in ((destination.title, 0, 4), (source.title, 3, logged_count))
        for index in range(count)
    ]
    _, items = build_card(
        destination,
        destination.title,
        history[:4],
        [policy],
        history_records=history,
        routines=[destination, source],
    )
    item = items[0]
    assert len(item.planned_sets) == (4 if logged_count == 3 else 2)
    assert item.last_reps == (() if progression == "duration" else (8,) * logged_count)
    if progression == "duration":
        assert [planned.duration_seconds for planned in item.planned_sets] == [45] * len(
            item.planned_sets
        )
    else:
        assert [planned.reps for planned in item.planned_sets] == [9] * len(item.planned_sets)

    # A genuinely incomplete session in the selected routine still stays incomplete.
    own_history = history[:4] + [
        replace(record, routine=destination.title) for record in history[4:]
    ]
    _, own_items = build_card(destination, destination.title, own_history, [policy])
    assert len(own_items[0].planned_sets) == logged_count


def test_unconfigured_card_uses_same_source_for_targets_warmups_and_date(tmp_path: Path) -> None:
    db = _seed_shared_history(tmp_path)
    source = tmp_path / "unconfigured.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "Open Session,2026-01-01 08:00:00,Core Machine,0,warmup,10,5,5\n"
        + "".join(
            f"Open Session,2026-01-01 08:00:00,Core Machine,{index},normal,50,8,8\n"
            for index in range(1, 4)
        ),
        encoding="utf-8",
    )
    with database(db) as connection:
        import_csv(connection, source, tmp_path / "imports")
    result = CliRunner().invoke(
        main,
        ["gym-card", "--all", "--workout", "Open Session", "--json", "--db", str(db)],
        env={"HEVY_API_KEY": ""},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    item = payload["exercises"][0]
    assert payload["source_workout_date"] == "2026-01-03"
    assert item["last_performance"]["weight_lbs"] == 75
    assert item["sets"][0]["weight_lbs"] == 20
    assert [planned["weight_lbs"] for planned in item["sets"][1:]] == [75] * 3
