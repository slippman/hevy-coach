import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from hevy_coach.cli import main
from hevy_coach.config import load_config, load_routine_policies
from hevy_coach.gym_card import build_card, freshness_line, render_card
from hevy_coach.importer import import_csv
from hevy_coach.query import records_for_workout
from hevy_coach.selector import choose_workout
from hevy_coach.storage import database

FIXTURE = Path(__file__).parent / "fixtures" / "current_workouts.csv"


def _seed(db: Path) -> None:
    with database(db) as connection:
        import_csv(connection, FIXTURE, db.parent / "imports")


@patch("hevy_coach.selector.questionary.select")
def test_questionary_selector_enables_arrow_keys_and_filter(mock_select) -> None:
    mock_select.return_value.ask.return_value = "PF:Chest & Arms"

    selected = choose_workout(["PF:Chest & Arms", "PF:Back & Arms"])

    assert selected == "PF:Chest & Arms"
    assert mock_select.call_args.kwargs["use_arrow_keys"] is True
    assert mock_select.call_args.kwargs["use_search_filter"] is True


@patch("hevy_coach.cli.choose_workout", return_value="PF:Chest & Arms")
@patch("hevy_coach.cli._is_interactive", return_value=True)
def test_interactive_selector_receives_configured_routines_in_recent_order(
    _, chooser, tmp_path: Path
) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)

    result = CliRunner().invoke(main, ["gym-card", "--no-clipboard", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert chooser.call_args.args[0] == ["PF:Back & Arms", "PF:Chest & Arms"]
    assert result.output.startswith("PF: Chest & Arms\n")


@patch("hevy_coach.cli.choose_workout", return_value=None)
@patch("hevy_coach.cli._is_interactive", return_value=True)
def test_selector_cancellation_is_clean(_, __, tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)

    result = CliRunner().invoke(main, ["gym-card", "--db", str(db)])

    assert result.exit_code != 0
    assert "Cancelled." in result.output
    assert "Traceback" not in result.output


def test_exact_partial_and_noninteractive_matching(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)
    runner = CliRunner()

    exact = runner.invoke(
        main, ["gym-card", "--workout", "PF:Chest & Arms", "--no-clipboard", "--db", str(db)]
    )
    partial = runner.invoke(
        main, ["gym-card", "--workout", "chest", "--no-clipboard", "--db", str(db)]
    )
    multiple = runner.invoke(main, ["gym-card", "--workout", "PF", "--db", str(db)])
    missing = runner.invoke(main, ["gym-card", "--workout", "nonsense", "--db", str(db)])
    noninteractive = runner.invoke(main, ["gym-card", "--db", str(db)])

    assert exact.exit_code == 0
    assert partial.exit_code == 0
    assert partial.output.startswith("PF: Chest & Arms")
    assert multiple.exit_code != 0 and "Multiple workouts match" in multiple.output
    assert missing.exit_code != 0 and "No stored workout matches" in missing.output
    assert noninteractive.exit_code != 0 and "Use --workout" in noninteractive.output


@pytest.mark.parametrize("title", ["PF: Back & Arms", "PF:Back & Arms", "PF:Back& Arms"])
def test_back_and_arms_title_aliases_resolve_to_the_configured_routine(
    title: str, tmp_path: Path
) -> None:
    source = tmp_path / "back-and-arms.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        f"{title},2024-01-10 18:00:00,Crunch (Machine),0,normal,90,10,7\n"
        f"{title},2024-01-10 18:00:00,Crunch (Machine),1,normal,90,10,7\n"
        f"{title},2024-01-10 18:00:00,Crunch (Machine),2,normal,90,10,7\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(main, ["gym-card", "--workout", title, "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("PF: Back & Arms\n")


def test_all_exposes_otherwise_hidden_routine(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)
    runner = CliRunner()

    hidden = runner.invoke(main, ["gym-card", "--workout", "Other", "--db", str(db)])
    shown = runner.invoke(
        main, ["gym-card", "--all", "--workout", "Other", "--no-clipboard", "--db", str(db)]
    )

    assert hidden.exit_code != 0
    assert shown.exit_code == 0
    assert shown.output.startswith("Other Routine")


@patch("hevy_coach.cli.clipboard.copy")
def test_default_stdout_and_clipboard_modes(mock_copy, tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)
    runner = CliRunner()

    default = runner.invoke(main, ["gym-card", "--workout", "chest", "--db", str(db)])

    assert default.exit_code == 0
    assert default.output.startswith("PF: Chest & Arms\n")
    assert not mock_copy.called
    copied = runner.invoke(main, ["gym-card", "--workout", "chest", "--clipboard", "--db", str(db)])

    assert copied.exit_code == 0
    assert copied.stdout == ""
    assert 'Copied "PF: Chest & Arms" gym card to clipboard.' in copied.stderr
    assert mock_copy.call_args.args[0].endswith("\n")


@patch("hevy_coach.cli.clipboard.copy", side_effect=subprocess.CalledProcessError(1, "pbcopy"))
def test_clipboard_failure_does_not_print_card(_, tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)

    result = CliRunner().invoke(
        main, ["gym-card", "--workout", "chest", "--clipboard", "--db", str(db)]
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Clipboard copy failed" in result.stderr


def test_json_conflicts_and_deterministic_format(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)
    runner = CliRunner()

    structured = runner.invoke(main, ["gym-card", "--workout", "chest", "--json", "--db", str(db)])
    conflict = runner.invoke(
        main, ["gym-card", "--workout", "chest", "--clipboard", "--json", "--db", str(db)]
    )
    first = runner.invoke(main, ["gym-card", "--workout", "chest", "--db", str(db)])
    second = runner.invoke(main, ["gym-card", "--workout", "chest", "--db", str(db)])

    payload = json.loads(structured.stdout)
    assert payload["workout"] == "PF: Chest & Arms"
    assert payload["exercises"][0]["sets"][0] == {"set": 1, "weight_lbs": 20.0, "reps": 10}
    assert structured.stdout.lstrip().startswith("{")
    assert conflict.exit_code != 0 and "cannot be combined" in conflict.output
    assert first.output == second.output
    assert first.output.endswith("\n") and not first.output.endswith("\n\n")


def test_card_order_and_configured_warmups(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)
    _, policies = load_config()
    routine = load_routine_policies()[0]
    with database(db) as connection:
        records = records_for_workout(connection, "PF:Chest & Arms")

    title, items = build_card(routine, "PF:Chest & Arms", records, policies)
    rendered = render_card(title, items)

    assert [item.exercise for item in items][:3] == ["Bench Press", "Shoulder Press", "Cable Fly"]
    assert "Bench Press\nSET   LBS   REPS\n1     20    10\n2     50    8" in rendered
    assert "Cable Fly\nSET   LBS   REPS\n1     15" in rendered
    assert "Warm-up:" not in rendered


def test_gym_card_uses_each_exercises_latest_session_after_partial_workouts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "partial-chest.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "PF:Chest & Arms,2026-08-28 08:00:00,Cable Fly,0,normal,10,10,8\n"
        "PF:Chest & Arms,2026-08-28 08:00:00,Cable Fly,1,normal,10,10,8\n"
        "PF:Chest & Arms,2026-08-28 08:00:00,Cable Fly,2,normal,10,10,8\n"
        "PF:Chest & Arms,2026-08-28 08:00:00,Lateral Raise,0,normal,10,12,8\n"
        "PF:Chest & Arms,2026-08-28 08:00:00,Lateral Raise,1,normal,10,12,8\n"
        "PF:Chest & Arms,2026-08-28 08:00:00,Lateral Raise,2,normal,10,12,8\n"
        "PF:Chest & Arms,2026-08-28 12:00:00,Dumbbell Bench Press,0,warmup,25,8,6\n"
        "PF:Chest & Arms,2026-08-28 12:00:00,Dumbbell Bench Press,1,normal,45,9,8\n"
        "PF:Chest & Arms,2026-08-28 12:00:00,Dumbbell Bench Press,2,normal,45,9,8\n"
        "PF:Chest & Arms,2026-08-28 12:00:00,Dumbbell Bench Press,3,normal,45,9,8\n"
        "PF:Chest & Arms,2026-08-28 12:00:00,Seated Cable Row,0,warmup,65,8,6\n"
        "PF:Chest & Arms,2026-08-28 12:00:00,Seated Cable Row,1,normal,105,10,8\n"
        "PF:Chest & Arms,2026-08-28 12:00:00,Seated Cable Row,2,normal,105,10,8\n"
        "PF:Chest & Arms,2026-08-28 12:00:00,Seated Cable Row,3,normal,105,10,8\n"
        "PF:Chest & Arms,2026-08-28 12:00:00,Seated Cable Row,4,normal,105,10,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")
        records = records_for_workout(connection, "PF:Chest & Arms")

    _, policies = load_config()
    routine = load_routine_policies()[0]
    title, items = build_card(routine, "PF:Chest & Arms", records, policies)
    rendered = render_card(title, items)

    assert [item.exercise for item in items] == [
        "Bench Press",
        "Seated Cable Row",
        "Cable Fly",
        "Lateral Raise",
    ]
    assert "Cable Fly\nSET   LBS   REPS\n1     10    10" in rendered
    assert "Lateral Raise\nSET   LBS   REPS\n1     10    12" in rendered


def test_workout_history_keeps_partial_sessions_separate(tmp_path: Path) -> None:
    source = tmp_path / "two-parts.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "PF:Chest & Arms,2026-08-28 08:00:00,Cable Fly,0,normal,10,10,8\n"
        "PF:Chest & Arms,2026-08-28 12:00:00,Dumbbell Bench Press,0,normal,45,9,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(main, ["workout", "history", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert result.output.count("PF:Chest & Arms") == 2


@patch("hevy_coach.cli.datetime")
def test_partial_card_freshness_uses_oldest_exercise_session(mock_datetime, tmp_path: Path) -> None:
    mock_datetime.now.return_value = datetime(2026, 8, 13, 12, tzinfo=UTC)
    source = tmp_path / "mixed-freshness.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "PF:Chest & Arms,2026-08-04 08:00:00,Cable Fly,0,normal,10,10,8\n"
        "PF:Chest & Arms,2026-08-04 08:00:00,Cable Fly,1,normal,10,10,8\n"
        "PF:Chest & Arms,2026-08-04 08:00:00,Cable Fly,2,normal,10,10,8\n"
        "PF:Chest & Arms,2026-08-12 12:00:00,Dumbbell Bench Press,0,normal,45,9,8\n"
        "PF:Chest & Arms,2026-08-12 12:00:00,Dumbbell Bench Press,1,normal,45,9,8\n"
        "PF:Chest & Arms,2026-08-12 12:00:00,Dumbbell Bench Press,2,normal,45,9,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(
        main, ["gym-card", "--workout", "chest", "--no-clipboard", "--db", str(db)]
    )

    assert result.exit_code == 0, result.output
    assert "⚠ Based on: Aug 4, 2026 (9 days ago)" in result.stdout
    assert "Some exercise history may be stale." in result.stderr


def test_freshness_line_warns_only_after_seven_days() -> None:
    source = date(2026, 8, 4)

    at_seven, seven_is_stale = freshness_line(source, date(2026, 8, 11))
    at_eight, eight_is_stale = freshness_line(source, date(2026, 8, 12))

    assert at_seven == "Based on: Aug 4, 2026 (7 days ago)"
    assert not seven_is_stale
    assert at_eight == "Based on: Aug 4, 2026 (8 days ago)"
    assert eight_is_stale


@patch("hevy_coach.cli.datetime")
def test_gym_card_freshness_uses_local_date_before_utc_midnight(
    mock_datetime, tmp_path: Path
) -> None:
    """Aug. 11 remains seven days old on Aug. 18 in the local time zone."""
    mock_datetime.now.return_value = datetime(2026, 8, 19, 0, 10, tzinfo=UTC)
    source = tmp_path / "workouts.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "PF:Chest & Arms,2026-08-11 18:00:00,Dumbbell Bench Press,0,normal,45,10,8\n"
        "PF:Chest & Arms,2026-08-11 18:00:00,Dumbbell Bench Press,1,normal,45,10,8\n"
        "PF:Chest & Arms,2026-08-11 18:00:00,Dumbbell Bench Press,2,normal,45,10,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(
        main, ["gym-card", "--workout", "chest", "--no-clipboard", "--db", str(db)]
    )

    assert result.exit_code == 0, result.output
    assert "Based on: Aug 11, 2026 (7 days ago)" in result.stdout
    assert "Some exercise history may be stale." not in result.stderr


@patch("hevy_coach.cli.datetime")
@patch("hevy_coach.cli.clipboard.copy")
def test_card_uses_selected_routine_date_and_surfaces_staleness_in_clipboard_mode(
    mock_copy, mock_date, tmp_path: Path
) -> None:
    mock_date.now.return_value = datetime(2026, 8, 12, 12, tzinfo=UTC)
    source = tmp_path / "workouts.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "PF:Back& Arms,2026-08-04 18:00:00,Crunch (Machine),0,normal,90,10,7\n"
        "PF:Back& Arms,2026-08-04 18:00:00,Crunch (Machine),1,normal,90,10,7\n"
        "PF:Back& Arms,2026-08-04 18:00:00,Crunch (Machine),2,normal,90,10,7\n"
        "PF:Chest & Arms,2026-08-10 18:00:00,Dumbbell Bench Press,0,normal,45,8,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(
        main, ["gym-card", "--workout", "back", "--clipboard", "--db", str(db)]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert 'Copied "PF: Back & Arms" gym card to clipboard.' in result.stderr
    assert "Some exercise history may be stale." in result.stderr
    assert "⚠ Based on: Aug 4, 2026 (8 days ago)" in mock_copy.call_args.args[0]
