"""Click command-line interface for the persistent Hevy coach."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import click

from .clipboard import Clipboard
from .coach import working_sets
from .config import load_config, load_routine_policies, resolve_routine
from .gym_card import build_card, card_json, freshness_line, render_card, unknown_routine_exercises
from .importer import import_csv
from .persistent_report import dated_filename, markdown, report_payload
from .query import (
    all_records,
    exercise_history,
    latest_imported_at,
    latest_workout_records,
    latest_workout_summary,
    recent_workouts,
    records_for_workout,
    records_for_workouts,
    workout_titles,
    workout_types,
)
from .selector import choose_workout
from .storage import DEFAULT_DB_PATH, database

clipboard = Clipboard()


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def _db_option(function):
    return click.option(
        "--db", type=click.Path(path_type=Path), default=DEFAULT_DB_PATH, show_default=True
    )(function)


@click.group()
def main() -> None:
    """Persist Hevy exports and generate next-session coaching."""


@main.command("import")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_db_option
def import_command(source: Path, db: Path) -> None:
    """Archive and idempotently import a Hevy CSV export."""
    with database(db) as connection:
        result = import_csv(connection, source, db.parent / "imports")
    if result.skipped:
        click.echo(f"Skipped: identical file was already imported ({result.source_hash[:12]}).")
        return
    click.echo(
        f"Imported {source.name}: {result.workouts_added} workouts, {result.exercises_added} exercises, "
        f"{result.sets_added} sets added. Archive: {result.archive}"
    )


@main.command()
@click.option("--latest", is_flag=True, default=True, help="report the newest imported workout")
@click.option("--clipboard", is_flag=True, help="copy Markdown to the macOS clipboard")
@click.option("--json", "as_json", is_flag=True, help="write structured JSON to stdout")
@click.option("--config", type=click.Path(exists=True, path_type=Path), default=None)
@_db_option
def report(latest: bool, clipboard: bool, as_json: bool, config: Path | None, db: Path) -> None:
    """Save and print a report for the latest workout."""
    del latest
    _, policies = load_config(config)
    routines = load_routine_policies(config)
    with database(db) as connection:
        latest_records = latest_workout_records(connection)
        if not latest_records:
            raise click.ClickException(
                "No imported workouts available; run `hevy-coach import PATH` first."
            )
        routine = resolve_routine(latest_records[0].routine, routines)
        if routine:
            policy_by_name = {policy.name: policy for policy in policies}
            routine_policies = [policy_by_name[name] for name in routine.exercises]
            history_records = records_for_workouts(connection, [routine.title, *routine.aliases])
        else:
            latest_names = {record.exercise.casefold() for record in latest_records}
            routine_policies = [
                policy
                for policy in policies
                if {policy.name.casefold(), *(alias.casefold() for alias in policy.aliases)}
                & latest_names
            ]
            history_records = all_records(connection)
        payload = report_payload(
            latest_records,
            routine_policies,
            history_records=history_records,
            routine=routine,
        )
    rendered = markdown(payload)
    reports = db.parent / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "latest.md").write_text(rendered, encoding="utf-8")
    (reports / dated_filename(payload)).write_text(rendered, encoding="utf-8")
    if clipboard:
        subprocess.run(["pbcopy"], input=rendered, text=True, check=True)
    click.echo(json.dumps(payload, indent=2) if as_json else rendered, nl=not as_json)


def _normalized(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _choose_workout(titles: list[str], requested: str | None, json_output: bool = False) -> str:
    if requested:
        exact = [title for title in titles if title.casefold() == requested.casefold()]
        matches = exact or [
            title for title in titles if _normalized(requested) in _normalized(title)
        ]
        if not matches:
            raise click.ClickException(f"No stored workout matches {requested!r}.")
    else:
        matches = titles
    if requested and len(matches) == 1:
        return matches[0]
    if not _is_interactive():
        if not requested:
            raise click.ClickException("Use --workout when gym-card is run non-interactively.")
        choices = ", ".join(matches)
        raise click.ClickException(f"Multiple workouts match; use --workout exactly: {choices}")
    try:
        selected = choose_workout(matches, output=sys.stderr if json_output else None)
    except KeyboardInterrupt:
        selected = None
    if selected is None:
        raise click.ClickException("Cancelled.")
    return selected


@main.command("gym-card")
@click.option("--workout", help="exact or partial stored workout title")
@click.option("--all", "include_all", is_flag=True, help="include non-configured workout titles")
@click.option(
    "--clipboard", "copy_to_clipboard", is_flag=True, help="copy card to the macOS clipboard"
)
@click.option(
    "--stdout", "explicit_stdout", is_flag=True, help="explicitly print the card to stdout"
)
@click.option(
    "--no-clipboard", is_flag=True, help="deprecated compatibility alias; card already prints"
)
@click.option("--json", "as_json", is_flag=True, help="emit structured JSON")
@click.option("--config", type=click.Path(exists=True, path_type=Path), default=None)
@_db_option
def gym_card(
    workout: str | None,
    include_all: bool,
    copy_to_clipboard: bool,
    explicit_stdout: bool,
    no_clipboard: bool,
    as_json: bool,
    config: Path | None,
    db: Path,
) -> None:
    """Build a compact next-session card for a configured routine."""
    if copy_to_clipboard and (explicit_stdout or as_json or no_clipboard):
        raise click.UsageError(
            "--clipboard cannot be combined with --stdout, --json, or --no-clipboard."
        )
    _, policies = load_config(config)
    routines = load_routine_policies(config)
    configured = {title for routine in routines for title in (routine.title, *routine.aliases)}
    with database(db) as connection:
        titles = workout_titles(connection)
        visible = titles if include_all else [title for title in titles if title in configured]
        selected = _choose_workout(
            visible if not workout or not include_all else titles, workout, json_output=as_json
        )
    routine = resolve_routine(selected, routines)
    with database(db) as connection:
        records = (
            records_for_workouts(connection, [routine.title, *routine.aliases])
            if routine
            else records_for_workout(connection, selected)
        )
    title, items = build_card(routine, selected, records, policies)
    if not items:
        raise click.ClickException(f"No configured strength exercises found for {selected!r}.")
    unknown = unknown_routine_exercises(routine, records, policies)
    source_date = max(record.started_at for record in records).date()
    today = datetime.now(UTC).date()
    _, stale = freshness_line(source_date, today)
    rendered = render_card(title, items, source_date=source_date, today=today)
    if as_json:
        payload = card_json(title, items, unknown)
        payload["source_workout_date"] = source_date.isoformat()
        payload["source_workout_age_days"] = (today - source_date).days
        payload["source_workout_stale"] = stale
        click.echo(json.dumps(payload, indent=2))
    elif copy_to_clipboard:
        try:
            clipboard.copy(rendered)
        except (OSError, subprocess.CalledProcessError) as error:
            raise click.ClickException(f"Clipboard copy failed: {error}") from error
        click.echo(f'Copied "{title}" gym card to clipboard.', err=True)
    else:
        click.echo(rendered, nl=False)
    if unknown and not as_json:
        click.echo(
            f"Review routine configuration; skipped unknown exercises: {', '.join(unknown)}.",
            err=True,
        )
    if stale and not as_json:
        click.echo("Latest Hevy export may not be imported.", err=True)


def _show_exercise_history(exercise: str, limit: int, db: Path) -> None:
    _, policies = load_config()
    policy = next(
        (
            item
            for item in policies
            if exercise.casefold()
            in {item.name.casefold(), *(alias.casefold() for alias in item.aliases)}
        ),
        None,
    )
    names = [policy.name, *policy.aliases] if policy else [exercise]
    with database(db) as connection:
        records = exercise_history(connection, names)
    if not records:
        raise click.ClickException(f"No history found for {exercise!r}.")
    sessions: dict[tuple[str, str], list] = {}
    for record in records:
        sessions.setdefault((record.started_at.date().isoformat(), record.routine), []).append(
            record
        )
    rows = []
    for (day, title), sets in list(sessions.items())[:limit]:
        working = working_sets(sets, policy.sets if policy else None)
        reps = sum(item.reps or 0 for item in working)
        weighted = [item for item in working if item.weight is not None and item.reps is not None]
        volume = sum(item.weight * item.reps for item in weighted) if weighted else None
        estimate = max((item.weight * (1 + item.reps / 30) for item in weighted), default=None)
        last_rpe = next((item.rpe for item in reversed(working) if item.rpe is not None), None)
        set_text = "/".join(str(item.reps) if item.reps is not None else "—" for item in working)
        rows.append((day, title, set_text, last_rpe, reps, volume, estimate))
    click.echo(
        "Date       Workout                 Working sets                 Last RPE  Reps  Volume  e1RM  Change"
    )
    prior_volume = None
    for day, title, set_text, last_rpe, reps, volume, estimate in reversed(rows):
        change = "—" if prior_volume is None or volume is None else f"{volume - prior_volume:+g}"
        click.echo(
            f"{day}  {title[:22]:22}  {set_text[:27]:27}  {last_rpe or '—'!s:8}  {reps:4}  "
            f"{volume if volume is not None else '—'!s:6}  {estimate if estimate is not None else '—'!s:5}  {change}"
        )
        prior_volume = volume
    estimates = [row[-1] for row in rows if row[-1] is not None]
    if len(estimates) > 1:
        click.echo(
            f"Trend ({len(estimates)} sessions): estimated 1RM {estimates[-1] - estimates[0]:+g} lb."
        )


@main.group("workout")
def workout() -> None:
    """Browse stored workout sessions."""


@workout.command("history")
@click.option("--limit", default=10, show_default=True, type=click.IntRange(1))
@_db_option
def workout_history(limit: int, db: Path) -> None:
    """Show recent workouts, newest first."""
    with database(db) as connection:
        workouts = recent_workouts(connection, limit)
    if not workouts:
        raise click.ClickException("No workouts found. Import a Hevy CSV first.")
    title_width = max(len("Workout"), *(len(item.title) for item in workouts))
    click.echo(f"Date        {'Workout':{title_width}}  Duration  Exercises  Sets")
    for item in workouts:
        duration = "—" if item.duration_seconds is None else f"{item.duration_seconds // 60} min"
        click.echo(
            f"{item.started_at.date().isoformat()}  {item.title:{title_width}}  {duration:8}  "
            f"{item.exercise_count:9}  {item.set_count:4}"
        )


@workout.command("list")
@_db_option
def workout_list(db: Path) -> None:
    """List unique workout titles and their logged session counts."""
    with database(db) as connection:
        types = workout_types(connection)
    if not types:
        raise click.ClickException("No workouts found. Import a Hevy CSV first.")
    title_width = max(len("Workout"), *(len(item.title) for item in types))
    click.echo(f"{'Workout':{title_width}}  Sessions  Last done   Total sets")
    for item in types:
        click.echo(
            f"{item.title:{title_width}}  {item.session_count:8}  "
            f"{item.last_started_at.date().isoformat()}  {item.set_count:10}"
        )


@main.group("exercise")
def exercise() -> None:
    """Browse exercise history."""


@exercise.command("history")
@click.argument("exercise")
@click.option("--limit", default=10, show_default=True, type=click.IntRange(1))
@_db_option
def exercise_history_command(exercise: str, limit: int, db: Path) -> None:
    """Show prior sessions for an exercise title or configured alias."""
    _show_exercise_history(exercise, limit, db)


@main.command()
@_db_option
def status(db: Path) -> None:
    """Show database and import status."""
    with database(db) as connection:
        imports = connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
        workouts = connection.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]
        exercises = connection.execute("SELECT COUNT(*) FROM exercises").fetchone()[0]
        sets = connection.execute("SELECT COUNT(*) FROM sets").fetchone()[0]
        latest_workout = latest_workout_summary(connection)
        latest_import = latest_imported_at(connection)
    lines = [
        f"Database: {db}",
        f"Imports: {imports}",
        f"Workouts: {workouts}",
        f"Exercises: {exercises}",
        f"Sets: {sets}",
    ]
    if latest_workout:
        lines.extend(
            [
                "",
                f"Latest workout: {latest_workout.started_at.date().isoformat()} — {latest_workout.title}",
            ]
        )
    if latest_import:
        lines.append(f"Latest import: {latest_import.date().isoformat()}")
    click.echo("\n".join(lines))


@main.command()
@click.argument("destination", type=click.Path(path_type=Path), required=False)
@_db_option
def backup(destination: Path | None, db: Path) -> None:
    """Copy the SQLite database to a backup path."""
    if not db.exists():
        raise click.ClickException("No database exists yet; import a CSV first.")
    target = destination or db.parent / "backups" / "hevy.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db, target)
    click.echo(f"Backup written to {target}")
