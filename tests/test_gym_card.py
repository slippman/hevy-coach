import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from hevy_coach.cli import main
from hevy_coach.config import load_config, load_routine_policies
from hevy_coach.gym_card import CardItem, CardSet, build_card, freshness_line, render_card
from hevy_coach.importer import import_csv
from hevy_coach.models import SetRecord
from hevy_coach.query import records_for_workout
from hevy_coach.selector import choose_workout
from hevy_coach.storage import database

FIXTURE = Path(__file__).parent / "fixtures" / "current_workouts.csv"


def _seed(db: Path) -> None:
    with database(db) as connection:
        import_csv(connection, FIXTURE, db.parent / "imports")


@patch("hevy_coach.selector.questionary.select")
def test_questionary_selector_enables_arrow_keys_and_filter(mock_select) -> None:
    mock_select.return_value.ask.return_value = "Strength A"

    selected = choose_workout(["Strength A", "Strength-B"])

    assert selected == "Strength A"
    assert mock_select.call_args.kwargs["use_arrow_keys"] is True
    assert mock_select.call_args.kwargs["use_search_filter"] is True


@patch("hevy_coach.cli.choose_workout", return_value="Strength A")
@patch("hevy_coach.cli._is_interactive", return_value=True)
def test_interactive_selector_receives_configured_routines_in_recent_order(
    _, chooser, tmp_path: Path
) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)

    result = CliRunner().invoke(main, ["gym-card", "--no-clipboard", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert chooser.call_args.args[0] == ["Strength-B", "Strength A"]
    assert result.output.startswith("Strength A\n")


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
        main, ["gym-card", "--workout", "Strength A", "--no-clipboard", "--db", str(db)]
    )
    partial = runner.invoke(
        main, ["gym-card", "--workout", "ength A", "--no-clipboard", "--db", str(db)]
    )
    multiple = runner.invoke(main, ["gym-card", "--workout", "Strength", "--db", str(db)])
    missing = runner.invoke(main, ["gym-card", "--workout", "nonsense", "--db", str(db)])
    noninteractive = runner.invoke(main, ["gym-card", "--db", str(db)])

    assert exact.exit_code == 0
    assert partial.exit_code == 0
    assert partial.output.startswith("Strength A")
    assert multiple.exit_code != 0 and "Multiple workouts match" in multiple.output
    assert missing.exit_code != 0 and "No stored workout matches" in missing.output
    assert noninteractive.exit_code != 0 and "Use --workout" in noninteractive.output


def test_private_config_beside_database_is_loaded_automatically(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "config.toml").write_text(
        """[defaults.global]
sets = 3
min_reps = 8
max_reps = 10
increment_lbs = 5
[routines]
names = ["Private Routine"]
[workouts."Private Routine"]
display_name = "My Workout"
exercise_order = ["Bench Press (Dumbbell)"]
working_set_counts = { "Bench Press (Dumbbell)" = 4 }
[exercises."Bench Press (Dumbbell)"]
aliases = ["Dumbbell Bench Press"]
""",
        encoding="utf-8",
    )
    source = tmp_path / "workout.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        + "".join(
            f"Private Routine,2026-01-15 10:00:00,Dumbbell Bench Press,{index},normal,40,8,8\n"
            for index in range(4)
        ),
        encoding="utf-8",
    )
    db = data_dir / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, data_dir / "imports")

    result = CliRunner().invoke(main, ["gym-card", "--workout", "Private Routine", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("My Workout\n")
    assert "40×8×4" in result.output


@pytest.mark.parametrize("title", ["Strength B", "Strength-B", "StrengthB"])
def test_punctuation_variants_resolve_to_the_configured_routine(title: str, tmp_path: Path) -> None:
    source = tmp_path / "routine-b.csv"
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
    assert result.output.startswith("Strength B\n")


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

    default = runner.invoke(main, ["gym-card", "--workout", "Strength A", "--db", str(db)])

    assert default.exit_code == 0
    assert default.output.startswith("Strength A\n")
    assert not mock_copy.called
    copied = runner.invoke(
        main, ["gym-card", "--workout", "Strength A", "--clipboard", "--db", str(db)]
    )

    assert copied.exit_code == 0
    assert copied.stdout == ""
    assert 'Copied "Strength A" gym card to clipboard.' in copied.stderr
    assert mock_copy.call_args.args[0].endswith("\n")


@patch("hevy_coach.cli.clipboard.copy", side_effect=subprocess.CalledProcessError(1, "pbcopy"))
def test_clipboard_failure_does_not_print_card(_, tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)

    result = CliRunner().invoke(
        main, ["gym-card", "--workout", "Strength A", "--clipboard", "--db", str(db)]
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Clipboard copy failed" in result.stderr


def test_json_conflicts_and_deterministic_format(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)
    runner = CliRunner()

    structured = runner.invoke(
        main, ["gym-card", "--workout", "Strength A", "--json", "--db", str(db)]
    )
    conflict = runner.invoke(
        main,
        ["gym-card", "--workout", "Strength A", "--clipboard", "--json", "--db", str(db)],
    )
    first = runner.invoke(main, ["gym-card", "--workout", "Strength A", "--db", str(db)])
    second = runner.invoke(main, ["gym-card", "--workout", "Strength A", "--db", str(db)])

    payload = json.loads(structured.stdout)
    assert payload["workout"] == "Strength A"
    assert payload["exercises"][0]["reasoning_category"]
    assert payload["exercises"][0]["sets"][0] == {
        "set": 1,
        "weight_lbs": 20.0,
        "reps": 10,
        "duration_seconds": None,
    }
    assert structured.stdout.lstrip().startswith("{")
    assert conflict.exit_code != 0 and "cannot be combined" in conflict.output
    assert first.output == second.output
    assert first.output.endswith("\n") and not first.output.endswith("\n\n")


def test_explained_card_groups_similar_decisions_above_one_compact_table() -> None:
    items = [
        CardItem(
            "Row",
            105,
            "",
            planned_sets=tuple(CardSet(index, 105, 10) for index in range(1, 4)),
            reasoning_category="HOLD",
            last_weight=105,
            last_reps=(10, 10, 10),
            last_rpe=10,
        ),
        CardItem(
            "Shoulder Press",
            70,
            "",
            planned_sets=tuple(CardSet(index, 70, 10) for index in range(1, 4)),
            reasoning_category="HOLD",
            last_weight=70,
            last_reps=(10, 10, 10),
            last_rpe=10,
        ),
        CardItem(
            "Cable Fly",
            15,
            "",
            planned_sets=tuple(CardSet(index, 15, 10) for index in range(1, 4)),
            reasoning_category="ADD_REPS",
            last_weight=15,
            last_reps=(9, 9, 9),
            last_rpe=9,
        ),
    ]

    explained = render_card("Strength", items, explain=True)
    plain = render_card("Strength", items)

    assert "Row and Shoulder Press stay put" in explained
    assert "Cable Fly adds reps" in explained
    assert explained.count("WORKOUT") == 1
    assert "105×10×3" in explained and "15×10×3" in explained
    assert "COACH'S SUMMARY" not in plain
    assert "WORKOUT" in plain


def test_card_order_and_configured_warmups(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _seed(db)
    _, policies = load_config()
    routine = load_routine_policies()[0]
    with database(db) as connection:
        records = records_for_workout(connection, "Strength A")

    title, items = build_card(routine, "Strength A", records, policies)
    rendered = render_card(title, items)

    assert [item.exercise for item in items][:3] == ["Bench Press", "Shoulder Press", "Cable Fly"]
    assert "Bench Press" in rendered and "20×10" in rendered and "50×8×3" in rendered
    assert "Cable Fly" in rendered and "15×10×3" in rendered
    assert "Warm-up:" not in rendered


def test_gym_card_uses_each_exercises_latest_session_after_partial_workouts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "partial-routine.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "Strength A,2026-08-28 08:00:00,Cable Fly,0,normal,10,10,8\n"
        "Strength A,2026-08-28 08:00:00,Cable Fly,1,normal,10,10,8\n"
        "Strength A,2026-08-28 08:00:00,Cable Fly,2,normal,10,10,8\n"
        "Strength A,2026-08-28 08:00:00,Lateral Raise,0,normal,10,12,8\n"
        "Strength A,2026-08-28 08:00:00,Lateral Raise,1,normal,10,12,8\n"
        "Strength A,2026-08-28 08:00:00,Lateral Raise,2,normal,10,12,8\n"
        "Strength A,2026-08-28 12:00:00,Dumbbell Bench Press,0,warmup,25,8,6\n"
        "Strength A,2026-08-28 12:00:00,Dumbbell Bench Press,1,normal,45,9,8\n"
        "Strength A,2026-08-28 12:00:00,Dumbbell Bench Press,2,normal,45,9,8\n"
        "Strength A,2026-08-28 12:00:00,Dumbbell Bench Press,3,normal,45,9,8\n"
        "Strength A,2026-08-28 12:00:00,Seated Cable Row,0,warmup,65,8,6\n"
        "Strength A,2026-08-28 12:00:00,Seated Cable Row,1,normal,105,10,8\n"
        "Strength A,2026-08-28 12:00:00,Seated Cable Row,2,normal,105,10,8\n"
        "Strength A,2026-08-28 12:00:00,Seated Cable Row,3,normal,105,10,8\n"
        "Strength A,2026-08-28 12:00:00,Seated Cable Row,4,normal,105,10,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")
        records = records_for_workout(connection, "Strength A")

    _, policies = load_config()
    routine = load_routine_policies()[0]
    title, items = build_card(routine, "Strength A", records, policies)
    rendered = render_card(title, items)

    assert [item.exercise for item in items] == [
        "Bench Press",
        "Seated Cable Row",
        "Cable Fly",
        "Lateral Raise",
    ]
    assert "Cable Fly" in rendered and "10×10×3" in rendered
    assert "Lateral Raise" in rendered and "10×10×3" in rendered


def test_workout_history_keeps_partial_sessions_separate(tmp_path: Path) -> None:
    source = tmp_path / "two-parts.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "Strength A,2026-08-28 08:00:00,Cable Fly,0,normal,10,10,8\n"
        "Strength A,2026-08-28 12:00:00,Dumbbell Bench Press,0,normal,45,9,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(main, ["workout", "history", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert result.output.count("Strength A") == 2


def test_sprint_card_uses_bodyweight_duration_and_configured_superset() -> None:
    started_at = datetime(2026, 1, 15, 10, tzinfo=UTC)
    records = []
    for exercise, values, rpe in (
        ("Push Up", (10, 10, 10), 7),
        ("Pull Up", (3, 2, 2), 10),
    ):
        records.extend(
            SetRecord(
                "BodyweightCircuit",
                started_at,
                exercise,
                index,
                "normal",
                None,
                reps,
                rpe if index == 2 else None,
            )
            for index, reps in enumerate(values)
        )
    records.extend(
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
    )
    _, policies = load_config()
    routine = next(item for item in load_routine_policies() if item.title == "Bodyweight Circuit")

    title, items = build_card(routine, "BodyweightCircuit", records, policies)
    rendered = render_card(title, items)

    assert [item.exercise for item in items] == ["Push-Up", "Pull-Up", "Plank"]
    assert "SUPERSET · 3 ROUNDS · Push-Up → Pull-Up · REST 90–120 SEC" in rendered
    assert "Push-Up" in rendered and "10×3 reps" in rendered
    assert "Pull-Up" in rendered and "3/2/2 reps" in rendered
    assert "Plank" in rendered and "50s/55s/50s" in rendered
    assert "0 lb" not in rendered


def test_sprint_partial_sessions_use_each_exercises_latest_actual_session() -> None:
    first = datetime(2026, 1, 15, 10, tzinfo=UTC)
    second = datetime(2026, 1, 15, 14, tzinfo=UTC)
    records = [
        SetRecord("BodyweightCircuit", first, "Push Up", index, "normal", None, 10, 8)
        for index in range(3)
    ] + [
        SetRecord(
            "BodyweightCircuit",
            second,
            "Plank",
            index,
            "normal",
            None,
            None,
            None,
            duration_seconds=45,
        )
        for index in range(3)
    ]
    _, policies = load_config()
    routine = next(item for item in load_routine_policies() if item.title == "Bodyweight Circuit")

    _, items = build_card(routine, routine.title, records, policies)

    assert [item.exercise for item in items] == ["Push-Up", "Plank"]
    assert items[0].prescription == "3×10"
    assert items[1].prescription == "45s/45s/45s"


@patch("hevy_coach.cli.datetime")
def test_partial_card_freshness_uses_oldest_exercise_session(mock_datetime, tmp_path: Path) -> None:
    mock_datetime.now.return_value = datetime(2026, 8, 13, 12, tzinfo=UTC)
    source = tmp_path / "mixed-freshness.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "Strength A,2026-08-04 08:00:00,Cable Fly,0,normal,10,10,8\n"
        "Strength A,2026-08-04 08:00:00,Cable Fly,1,normal,10,10,8\n"
        "Strength A,2026-08-04 08:00:00,Cable Fly,2,normal,10,10,8\n"
        "Strength A,2026-08-12 12:00:00,Dumbbell Bench Press,0,normal,45,9,8\n"
        "Strength A,2026-08-12 12:00:00,Dumbbell Bench Press,1,normal,45,9,8\n"
        "Strength A,2026-08-12 12:00:00,Dumbbell Bench Press,2,normal,45,9,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(
        main, ["gym-card", "--workout", "Strength A", "--no-clipboard", "--db", str(db)]
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
        "Strength A,2026-08-11 18:00:00,Dumbbell Bench Press,0,normal,45,10,8\n"
        "Strength A,2026-08-11 18:00:00,Dumbbell Bench Press,1,normal,45,10,8\n"
        "Strength A,2026-08-11 18:00:00,Dumbbell Bench Press,2,normal,45,10,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(
        main, ["gym-card", "--workout", "Strength A", "--no-clipboard", "--db", str(db)]
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
        "StrengthB,2026-08-04 18:00:00,Crunch (Machine),0,normal,90,10,7\n"
        "StrengthB,2026-08-04 18:00:00,Crunch (Machine),1,normal,90,10,7\n"
        "StrengthB,2026-08-04 18:00:00,Crunch (Machine),2,normal,90,10,7\n"
        "Strength A,2026-08-10 18:00:00,Dumbbell Bench Press,0,normal,45,8,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(
        main, ["gym-card", "--workout", "StrengthB", "--clipboard", "--db", str(db)]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert 'Copied "Strength B" gym card to clipboard.' in result.stderr
    assert "Some exercise history may be stale." in result.stderr
    assert "⚠ Based on: Aug 4, 2026 (8 days ago)" in mock_copy.call_args.args[0]


def test_gym_card_explain_uses_the_same_hold_decision_as_the_card(tmp_path: Path) -> None:
    source = tmp_path / "explain.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        + "".join(
            f"Strength A,2026-01-0{session} 08:00:00,Dumbbell Bench Press,{index},normal,"
            f"{'25,8,5' if index == 0 else '45,10,9'}\n"
            for session in (1, 4)
            for index in range(4)
        ),
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")

    result = CliRunner().invoke(
        main,
        ["gym-card", "--workout", "Strength A", "--explain", "--db", str(db)],
    )

    assert result.exit_code == 0, result.output
    assert "45" in result.output
    assert "COACH'S SUMMARY" in result.output
    assert "Bench Press repeats 45×10×3" in result.output
    assert "last session reached RPE 9" in result.output
    assert result.output.count("Bench Press") == 2


@patch("hevy_coach.cli.datetime")
def test_recent_api_sync_distinguishes_old_routine_from_stale_database(
    mock_datetime, tmp_path: Path
) -> None:
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    mock_datetime.now.return_value = now
    source = tmp_path / "old.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "Strength A,2026-08-01 08:00:00,Dumbbell Bench Press,0,normal,45,8,8\n"
        "Strength A,2026-08-01 08:00:00,Dumbbell Bench Press,1,normal,45,8,8\n"
        "Strength A,2026-08-01 08:00:00,Dumbbell Bench Press,2,normal,45,8,8\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"
    with database(db) as connection:
        import_csv(connection, source, db.parent / "imports")
        connection.execute(
            "INSERT INTO sync_state(provider, cursor, synced_at) VALUES (?, ?, ?)",
            ("hevy_api", now.isoformat(), now.isoformat()),
        )
        connection.commit()

    runner = CliRunner()
    card = runner.invoke(main, ["gym-card", "--workout", "Strength A", "--db", str(db)])
    result = runner.invoke(main, ["gym-card", "--workout", "Strength A", "--json", "--db", str(db)])

    assert card.exit_code == 0, card.output
    assert "Based on: Aug 1, 2026 (19 days ago)" in card.stdout
    assert "⚠" not in card.stdout
    assert "stale" not in card.stderr.casefold()
    payload = json.loads(result.output)
    assert payload["routine_session_old"] is True
    assert payload["database_sync_fresh"] is True
    assert payload["history_may_be_stale"] is False
