import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from hevy_coach.cli import main
from hevy_coach.config import load_routine_policies

FIXTURE = Path(__file__).parent / "fixtures" / "current_workouts.csv"


def test_import_report_status_workout_list_and_exercise_history(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "data" / "hevy.db"

    imported = runner.invoke(main, ["import", str(FIXTURE), "--db", str(db)])
    assert imported.exit_code == 0, imported.output
    assert "3 workouts" in imported.output

    status = runner.invoke(main, ["status", "--db", str(db)])
    assert status.exit_code == 0
    assert "Workouts: 3" in status.output

    report = runner.invoke(main, ["report", "--latest", "--db", str(db), "--json"])
    assert report.exit_code == 0, report.output
    assert '"workout"' in report.output
    assert (db.parent / "reports" / "latest.md").exists()

    workouts = runner.invoke(main, ["workout", "history", "--db", str(db)])
    assert workouts.exit_code == 0, workouts.output
    assert "Date        Workout" in workouts.output
    assert workouts.output.index("Other Routine") < workouts.output.index("PF:Back & Arms")
    assert "Exercises" in workouts.output and "Sets" in workouts.output

    workout_types = runner.invoke(main, ["workout", "list", "--db", str(db)])
    assert workout_types.exit_code == 0, workout_types.output
    assert "Workout" in workout_types.output and "Sessions" in workout_types.output
    assert "Last done" in workout_types.output and "Total sets" in workout_types.output
    assert workout_types.output.index("Other Routine") < workout_types.output.index(
        "PF:Back & Arms"
    )

    history = runner.invoke(main, ["exercise", "history", "Seated Cable Row", "--db", str(db)])
    assert history.exit_code == 0
    assert "2024-01-10" in history.output

    legacy = runner.invoke(main, ["history", "Seated Cable Row", "--db", str(db)])
    assert legacy.exit_code != 0
    assert "No such command 'history'" in legacy.output


def test_report_scopes_recommendations_to_latest_routine_and_refreshes_progression(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    db = tmp_path / "hevy.db"
    baseline = tmp_path / "baseline.csv"
    baseline.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "PF: Back & Arms,2024-01-01 18:00:00,Incline Bench Press (Dumbbell),0,normal,40,8,7\n"
        "PF:Chest & Arms,2024-01-02 18:00:00,Dumbbell Bench Press,0,warmup,20,8,5\n"
        "PF:Chest & Arms,2024-01-02 18:00:00,Dumbbell Bench Press,1,normal,45,8,8\n"
        "PF:Chest & Arms,2024-01-02 18:00:00,Dumbbell Bench Press,2,normal,45,8,8\n"
        "PF:Chest & Arms,2024-01-02 18:00:00,Dumbbell Bench Press,3,normal,45,8,8\n",
        encoding="utf-8",
    )
    latest = tmp_path / "latest.csv"
    latest.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "PF:Chest & Arms,2024-01-04 18:00:00,Dumbbell Bench Press,0,warmup,20,8,5\n"
        "PF:Chest & Arms,2024-01-04 18:00:00,Dumbbell Bench Press,1,normal,45,9,8\n"
        "PF:Chest & Arms,2024-01-04 18:00:00,Dumbbell Bench Press,2,normal,45,9,8\n"
        "PF:Chest & Arms,2024-01-04 18:00:00,Dumbbell Bench Press,3,normal,45,9,8\n",
        encoding="utf-8",
    )

    assert runner.invoke(main, ["import", str(baseline), "--db", str(db)]).exit_code == 0
    first = runner.invoke(main, ["report", "--latest", "--json", "--db", str(db)])
    assert first.exit_code == 0, first.output
    assert runner.invoke(main, ["import", str(latest), "--db", str(db)]).exit_code == 0
    second = runner.invoke(main, ["report", "--latest", "--json", "--db", str(db)])

    assert second.exit_code == 0, second.output
    first_bench = json.loads(first.output)["recommendations"][0]
    payload = json.loads(second.output)
    recommendation_names = [item["exercise"] for item in payload["recommendations"]]
    chest = next(item for item in load_routine_policies() if item.title == "PF:Chest & Arms")
    bench = payload["recommendations"][0]

    assert payload["workout"]["title"] == "PF:Chest & Arms"
    assert recommendation_names == list(chest.exercises)
    assert "Incline Bench Press (Dumbbell)" not in recommendation_names
    assert bench["action"] == "increase weight"
    assert bench["weight"] == 50
    assert bench["message"] == "Increase one increment to 50 lb next time."
    assert bench["message"] != first_bench["message"]
    assert "confirm" not in bench["message"].casefold()


@patch("hevy_coach.cli.subprocess.run")
def test_report_copies_markdown_to_clipboard(mock_run, tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "hevy.db"
    assert runner.invoke(main, ["import", str(FIXTURE), "--db", str(db)]).exit_code == 0

    result = runner.invoke(main, ["report", "--clipboard", "--db", str(db)])

    assert result.exit_code == 0
    assert mock_run.called
    assert mock_run.call_args.args[0] == ["pbcopy"]


def test_status_shows_latest_workout_and_import(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "hevy.db"
    assert runner.invoke(main, ["import", str(FIXTURE), "--db", str(db)]).exit_code == 0

    result = runner.invoke(main, ["status", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert "Latest workout: 2024-01-11 — Other Routine" in result.output
    assert "Latest import:" in result.output


def test_status_handles_an_empty_database(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["status", "--db", str(tmp_path / "hevy.db")])

    assert result.exit_code == 0
    assert "Workouts: 0" in result.output
    assert "Latest workout:" not in result.output
    assert "Latest import:" not in result.output
