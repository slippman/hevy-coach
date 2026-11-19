import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from hevy_coach.cli import main
from hevy_coach.importer import import_csv
from hevy_coach.models import ExercisePolicy, RoutinePolicy
from hevy_coach.storage import database
from hevy_coach.workout_order import review_order


def _policies():
    return [
        ExercisePolicy(name, (alias,), 3, 8, 12, 5)
        for name, alias in (("Press", "DB Press"), ("Row", "Cable Row"), ("Curl", "DB Curl"))
    ]


@pytest.mark.parametrize(
    ("names", "changed", "normalized"),
    [
        (["Cable Row", "DB Press", "DB Curl"], (True, True, False), (1, 0, 2)),
        (["DB Press", "DB Curl"], (False, False), (0, 1)),
        (["Unknown", "Cable Row", "DB Curl"], (False, False, False), (1, 2, 0)),
        (["DB Curl", "Unknown", "DB Press"], (True, False, True), (2, 0, 1)),
        (["Press", "Press", "Row"], (False, False, False), (0, 1, 2)),
    ],
)
def test_relative_order_ignores_omissions_unknowns_and_duplicate_positions(
    names, changed, normalized
):
    routine = RoutinePolicy("Strength A", "Strength A", ("Press", "Row", "Curl"))
    result = review_order(names, routine, _policies())
    assert result.out_of_order == changed
    assert result.normalized_indices == normalized


def _seed(tmp_path: Path, title="Strength A"):
    (tmp_path / "config.toml").write_text(
        """[defaults.global]
sets = 3
min_reps = 8
max_reps = 12
increment_lbs = 5
[routines]
names = ["Strength A"]
[workouts."Strength A"]
exercise_order = ["Press", "Row", "Curl"]
[exercises."Press"]
aliases = ["DB Press"]
[exercises."Row"]
aliases = ["Cable Row"]
[exercises."Curl"]
aliases = ["DB Curl"]
""",
        encoding="utf-8",
    )
    source = tmp_path / "synthetic.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        + "".join(
            f"{title},2026-01-01 10:00:00,{name},{index},normal,{weight},8,8\n"
            for name, weight in (
                ("Cable Row", 40),
                ("DB Press", 30),
                ("DB Curl", 15),
                ("Unknown", 10),
            )
            for index in range(2)
        ),
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, tmp_path / "imports")
        connection.execute(
            "UPDATE exercises SET superset_id = 0 WHERE exercise_title = 'Cable Row'"
        )
        connection.commit()
    return db


def test_show_defaults_to_logged_order_and_flags_only_relative_changes(tmp_path):
    db = _seed(tmp_path)
    result = CliRunner().invoke(main, ["workout", "show", "1", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert result.output.index("Cable Row · Superset") < result.output.index(
        "DB Press · out of order"
    )
    assert "Cable Row · Superset 0 · out of order (logged #1; routine #2)" in result.output
    assert "DB Press · out of order (logged #2; routine #1)" in result.output
    assert "DB Curl · out of order" not in result.output
    assert "Unknown · out of order" not in result.output


def test_normalized_json_preserves_logged_positions_sets_and_supersets(tmp_path):
    db = _seed(tmp_path)
    runner = CliRunner()
    logged = runner.invoke(main, ["workout", "show", "1", "--json", "--db", str(db)])
    normalized = runner.invoke(
        main, ["workout", "show", "1", "--normalize-order", "--json", "--db", str(db)]
    )
    assert logged.exit_code == normalized.exit_code == 0
    actual = json.loads(logged.stdout)
    ordered = json.loads(normalized.stdout)
    assert actual["exercise_order"] == "logged"
    assert ordered["exercise_order"] == "routine"
    assert ordered["order_reference"] == "current_configuration"
    assert [item["exercise"] for item in actual["exercises"]] == [
        "Cable Row",
        "DB Press",
        "DB Curl",
        "Unknown",
    ]
    assert [item["exercise"] for item in ordered["exercises"]] == [
        "DB Press",
        "Cable Row",
        "DB Curl",
        "Unknown",
    ]
    assert [item["logged_position"] for item in ordered["exercises"]] == [2, 1, 3, 4]
    assert [item["routine_position"] for item in actual["exercises"]] == [2, 1, 3, None]
    assert sorted(actual["exercises"], key=lambda item: item["exercise"]) == sorted(
        ordered["exercises"], key=lambda item: item["exercise"]
    )
    repeated = runner.invoke(main, ["workout", "show", "1", "--json", "--db", str(db)])
    assert json.loads(repeated.stdout) == actual  # normalization never rewrites history


def test_normalized_terminal_output_and_unknown_routine(tmp_path):
    db = _seed(tmp_path)
    result = CliRunner().invoke(
        main, ["workout", "show", "1", "--normalize-order", "--db", str(db)]
    )
    assert result.exit_code == 0, result.output
    assert result.output.index("DB Press · out of order") < result.output.index(
        "Cable Row · Superset"
    )
    unknown_path = tmp_path / "other"
    unknown_path.mkdir()
    unknown_db = _seed(unknown_path, title="Open Session")
    logged = CliRunner().invoke(main, ["workout", "show", "1", "--json", "--db", str(unknown_db)])
    assert logged.exit_code == 0
    payload = json.loads(logged.stdout)
    assert payload["order_reference"] is None
    assert all(not item["out_of_order"] for item in payload["exercises"])
    normalized = CliRunner().invoke(
        main, ["workout", "show", "1", "--normalize-order", "--db", str(unknown_db)]
    )
    assert normalized.exit_code == 1
    assert "No configured routine order" in normalized.output
