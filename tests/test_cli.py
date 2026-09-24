import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from hevy_coach.cli import main
from hevy_coach.config import load_routine_policies
from hevy_coach.importer import import_csv
from hevy_coach.storage import database

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
    assert workouts.output.index("Other Routine") < workouts.output.index("Strength-B")
    assert "Exercises" in workouts.output and "Sets" in workouts.output

    workout_types = runner.invoke(main, ["workout", "list", "--db", str(db)])
    assert workout_types.exit_code == 0, workout_types.output
    assert "Workout" in workout_types.output and "Sessions" in workout_types.output
    assert "Last done" in workout_types.output and "Total sets" in workout_types.output
    assert workout_types.output.index("Other Routine") < workout_types.output.index("Strength-B")

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
        "Strength B,2024-01-01 18:00:00,Incline Bench Press (Dumbbell),0,normal,40,8,7\n"
        "Strength A,2024-01-02 18:00:00,Dumbbell Bench Press,0,warmup,20,8,5\n"
        "Strength A,2024-01-02 18:00:00,Dumbbell Bench Press,1,normal,45,8,8\n"
        "Strength A,2024-01-02 18:00:00,Dumbbell Bench Press,2,normal,45,8,8\n"
        "Strength A,2024-01-02 18:00:00,Dumbbell Bench Press,3,normal,45,8,8\n",
        encoding="utf-8",
    )
    latest = tmp_path / "latest.csv"
    latest.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "Strength A,2024-01-04 18:00:00,Dumbbell Bench Press,0,warmup,20,8,5\n"
        "Strength A,2024-01-04 18:00:00,Dumbbell Bench Press,1,normal,45,10,8\n"
        "Strength A,2024-01-04 18:00:00,Dumbbell Bench Press,2,normal,45,10,8\n"
        "Strength A,2024-01-04 18:00:00,Dumbbell Bench Press,3,normal,45,10,8\n",
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
    routine_a = next(item for item in load_routine_policies() if item.title == "Strength A")
    bench = payload["recommendations"][0]

    assert payload["workout"]["title"] == "Strength A"
    assert recommendation_names == list(routine_a.exercises)
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


def test_report_renders_duration_sets_in_seconds(tmp_path: Path) -> None:
    source = tmp_path / "timed.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,duration_seconds,rpe\n"
        "Bodyweight Circuit,2026-01-01 08:00:00,Plank,0,normal,45,7\n"
        "Bodyweight Circuit,2026-01-01 08:00:00,Plank,1,normal,50,7\n"
        "Bodyweight Circuit,2026-01-01 08:00:00,Plank,2,normal,55,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    runner = CliRunner()
    assert runner.invoke(main, ["import", str(source), "--db", str(db)]).exit_code == 0

    result = runner.invoke(main, ["report", "--latest", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert "Working: 45 sec, 50 sec, 55 sec" in result.output
    assert "bodyweight × —" not in result.output


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


def test_exercise_history_excludes_configured_unmarked_warmup_from_metrics(
    tmp_path: Path,
) -> None:
    source = tmp_path / "history.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "Strength A,2026-01-01 08:00:00,Dumbbell Bench Press,0,normal,25,8,5\n"
        "Strength A,2026-01-01 08:00:00,Dumbbell Bench Press,1,normal,45,8,8\n"
        "Strength A,2026-01-01 08:00:00,Dumbbell Bench Press,2,normal,45,8,8\n"
        "Strength A,2026-01-01 08:00:00,Dumbbell Bench Press,3,normal,45,8,8\n"
        "Strength A,2026-01-04 08:00:00,Dumbbell Bench Press,0,normal,25,8,5\n"
        "Strength A,2026-01-04 08:00:00,Dumbbell Bench Press,1,normal,45,10,8\n"
        "Strength A,2026-01-04 08:00:00,Dumbbell Bench Press,2,normal,45,10,8\n"
        "Strength A,2026-01-04 08:00:00,Dumbbell Bench Press,3,normal,45,10,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(main, ["exercise", "history", "Bench Press", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert "Warm-up: 25×8" in result.output
    assert "Working: 45×10/10/10" in result.output
    assert "Reps: 30" in result.output
    assert "Volume: 1350.0" in result.output
    assert "Trend (2 sessions): estimated 1RM +3 lb." in result.output


def test_exercise_lookup_suggests_names_and_list_supports_search(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "hevy.db"
    assert runner.invoke(main, ["import", str(FIXTURE), "--db", str(db)]).exit_code == 0

    miss = runner.invoke(main, ["exercise", "history", "Dumbell Bench Pres", "--db", str(db)])
    listing = runner.invoke(main, ["exercise", "list", "--search", "bench", "--db", str(db)])

    assert miss.exit_code != 0
    assert "Did you mean" in miss.output
    assert listing.exit_code == 0, listing.output
    assert "Dumbbell Bench Press" in listing.output
    assert "Lat Pulldown" not in listing.output


def test_exercise_history_combines_stored_configured_aliases(tmp_path: Path) -> None:
    source = tmp_path / "aliases.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "Strength A,2026-01-01 08:00:00,Dumbbell Bench Press,0,warmup,25,8,5\n"
        "Strength A,2026-01-01 08:00:00,Dumbbell Bench Press,1,normal,45,8,8\n"
        "Strength A,2026-01-01 08:00:00,Dumbbell Bench Press,2,normal,45,8,8\n"
        "Strength A,2026-01-01 08:00:00,Dumbbell Bench Press,3,normal,45,8,8\n"
        "Strength A,2026-01-08 08:00:00,DB Bench,0,warmup,25,8,5\n"
        "Strength A,2026-01-08 08:00:00,DB Bench,1,normal,45,9,8\n"
        "Strength A,2026-01-08 08:00:00,DB Bench,2,normal,45,9,8\n"
        "Strength A,2026-01-08 08:00:00,DB Bench,3,normal,45,9,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(main, ["exercise", "history", "DB Bench", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert "2026-01-01" in result.output
    assert "2026-01-08" in result.output
    assert "Trend (2 sessions)" in result.output


def test_workout_show_uses_stable_id_and_preserves_set_details(tmp_path: Path) -> None:
    source = tmp_path / "detail.csv"
    source.write_text(
        "title,start_time,end_time,exercise_title,set_index,set_type,weight_lbs,reps,duration_seconds,rpe\n"
        "Strength A,2026-01-01 08:00:00,2026-01-01 09:00:00,Dumbbell Bench Press,0,normal,25,8,,5\n"
        "Strength A,2026-01-01 08:00:00,2026-01-01 09:00:00,Dumbbell Bench Press,1,normal,45,10,,8\n"
        "Strength A,2026-01-01 08:00:00,2026-01-01 09:00:00,Plank,0,normal,,,45,7\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")
        workout_id = connection.execute("SELECT id FROM workouts").fetchone()[0]
        connection.execute("UPDATE exercises SET superset_id = 0 WHERE exercise_title = 'Plank'")
        connection.commit()

    runner = CliRunner()
    result = runner.invoke(main, ["workout", "show", str(workout_id), "--db", str(db)])
    payload_result = runner.invoke(
        main, ["workout", "show", str(workout_id), "--json", "--db", str(db)]
    )

    assert result.exit_code == 0, result.output
    assert f"ID: {workout_id}" in result.output
    assert "warm-up   25 lb × 8" in result.output
    assert "working   45 lb × 10" in result.output
    assert "Plank · Superset 0" in result.output
    assert "45 sec" in result.output
    payload = json.loads(payload_result.output)
    assert payload["exercises"][0]["sets"][0]["type"] == "warm-up"
    assert payload["exercises"][0]["sets"][0]["logged_type"] == "normal"
    assert payload["exercises"][1]["superset_id"] == 0


def test_workout_show_reports_ids_when_title_is_ambiguous(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "hevy.db"
    assert runner.invoke(main, ["import", str(FIXTURE), "--db", str(db)]).exit_code == 0

    result = runner.invoke(main, ["workout", "show", "Strength", "--db", str(db)])

    assert result.exit_code != 0
    assert "Multiple workouts match. Use a workout ID:" in result.output


@patch("hevy_coach.cli._is_interactive", return_value=True)
@patch("hevy_coach.cli.choose_workout")
def test_workout_show_offers_selector_when_match_is_ambiguous(chooser, _, tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "hevy.db"
    assert runner.invoke(main, ["import", str(FIXTURE), "--db", str(db)]).exit_code == 0
    with database(db) as connection:
        selected = connection.execute(
            "SELECT id, title, start_time FROM workouts WHERE title = 'Strength A'"
        ).fetchone()
    chooser.return_value = f"{selected['id']} · 2024-01-08 18:00 · {selected['title']}"

    result = runner.invoke(main, ["workout", "show", "Strength", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert f"ID: {selected['id']}" in result.output
    assert len(chooser.call_args.args[0]) == 2
