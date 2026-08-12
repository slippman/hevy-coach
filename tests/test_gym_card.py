import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from hevy_coach.cli import main
from hevy_coach.config import load_config, load_routine_policies
from hevy_coach.gym_card import build_card, render_card
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
