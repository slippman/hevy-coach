"""Click command-line interface for the persistent Hevy coach."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from difflib import get_close_matches
from pathlib import Path

import click

from .api_sync import sync_workouts
from .clipboard import Clipboard
from .coach import _matches, working_sets
from .config import load_config, load_routine_policies, resolve_routine
from .gym_card import (
    build_card,
    card_json,
    freshness_line,
    oldest_card_source_date,
    render_card,
    unknown_routine_exercises,
)
from .hevy_api import HevyAPI, HevyAPIError
from .importer import import_csv
from .persistent_report import dated_filename, markdown, report_payload
from .query import (
    all_records,
    exercise_history,
    exercise_summaries,
    latest_api_synced_at,
    latest_imported_at,
    latest_workout_records,
    latest_workout_summary,
    recent_workouts,
    records_for_workout,
    records_for_workouts,
    workout_set_details,
    workout_summary_by_id,
    workout_titles,
    workout_types,
)
from .selector import choose_workout
from .storage import DEFAULT_DB_PATH, database
from .time_utils import as_local, as_utc, local_date, timezone_name

clipboard = Clipboard()


def _config_for_database(config: Path | None, db: Path) -> Path | None:
    """Prefer an explicit config, then a private config beside the database."""
    if config is not None:
        return config
    local = db.parent / "config.toml"
    return local if local.exists() else None


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


@main.command("sync")
@click.option(
    "--since",
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z"]),
    help="override the incremental cursor (inclusive)",
)
@click.option("--all", "all_history", is_flag=True, help="sync complete Hevy workout history")
@_db_option
def sync_command(since: datetime | None, all_history: bool, db: Path) -> None:
    """Pull new and changed workouts from the Hevy Pro API."""
    if since is not None and all_history:
        raise click.UsageError("--since and --all cannot be combined.")
    api_key = os.environ.get("HEVY_API_KEY", "").strip()
    if not api_key:
        raise click.ClickException(
            "HEVY_API_KEY is not set. Get your Pro API key at "
            "https://hevy.com/settings?developer and export it in this terminal."
        )
    if all_history:
        start = datetime(1970, 1, 1, tzinfo=UTC)
    elif since is not None:
        start = as_utc(since, naive_is_local=True)
    else:
        start = None
    try:
        with database(db) as connection:
            result = sync_workouts(connection, HevyAPI(api_key), since=start)
    except (HevyAPIError, TypeError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    if not result.changed:
        click.echo("Hevy is already up to date.")
        return
    click.echo(
        f"Synced Hevy: {result.workouts_added} new, {result.workouts_updated} updated, "
        f"{result.workouts_deleted} deleted workouts."
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
    config = _config_for_database(config, db)
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
            matching_titles = [
                title
                for title in workout_titles(connection)
                if resolve_routine(title, [routine]) is not None
            ]
            history_records = records_for_workouts(connection, matching_titles)
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


def _format_logged_sets(sets) -> str:
    if not sets:
        return "—"
    if all(item.duration_seconds is not None for item in sets):
        return "/".join(f"{item.duration_seconds}s" for item in sets)
    weights = [item.weight for item in sets if item.weight is not None]
    reps = [item.reps for item in sets if item.reps is not None]
    if not reps:
        return "—"
    rep_text = "/".join(str(rep) for rep in reps)
    if weights and len(set(weights)) == 1:
        return f"{weights[0]:g}×{rep_text}"
    return " · ".join(
        (
            f"{item.weight:g}×{item.reps}"
            if item.weight is not None and item.reps is not None
            else str(item.reps or "—")
        )
        for item in sets
    )


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
@click.option("--explain", is_flag=True, help="show why each prescription was chosen")
@click.option("--config", type=click.Path(exists=True, path_type=Path), default=None)
@_db_option
def gym_card(
    workout: str | None,
    include_all: bool,
    copy_to_clipboard: bool,
    explicit_stdout: bool,
    no_clipboard: bool,
    as_json: bool,
    explain: bool,
    config: Path | None,
    db: Path,
) -> None:
    """Build a compact next-session card for a configured routine."""
    if copy_to_clipboard and (explicit_stdout or as_json or no_clipboard):
        raise click.UsageError(
            "--clipboard cannot be combined with --stdout, --json, or --no-clipboard."
        )
    config = _config_for_database(config, db)
    _, policies = load_config(config)
    routines = load_routine_policies(config)
    with database(db) as connection:
        titles = workout_titles(connection)
        visible = (
            titles
            if include_all
            else [title for title in titles if resolve_routine(title, routines) is not None]
        )
        selected = _choose_workout(
            visible if not workout or not include_all else titles, workout, json_output=as_json
        )
    routine = resolve_routine(selected, routines)
    with database(db) as connection:
        matching_titles = (
            [
                title
                for title in workout_titles(connection)
                if routine is not None and resolve_routine(title, [routine]) is not None
            ]
            if routine
            else []
        )
        records = (
            records_for_workouts(connection, matching_titles)
            if routine
            else records_for_workout(connection, selected)
        )
        last_sync = latest_api_synced_at(connection)
    title, items = build_card(routine, selected, records, policies)
    if not items:
        raise click.ClickException(f"No configured strength exercises found for {selected!r}.")
    unknown = unknown_routine_exercises(routine, records, policies)
    source_date = oldest_card_source_date(items) or local_date(
        max(record.started_at for record in records)
    )
    today = local_date(datetime.now().astimezone())
    now_utc = as_utc(datetime.now().astimezone())
    _, stale = freshness_line(source_date, today)
    sync_fresh = bool(
        last_sync is not None and timedelta(0) <= now_utc - as_utc(last_sync) <= timedelta(hours=24)
    )
    history_may_be_stale = stale and not sync_fresh
    rendered = render_card(
        title,
        items,
        source_date=source_date,
        today=today,
        explain=explain,
        warn_if_old=history_may_be_stale,
    )
    if as_json:
        payload = card_json(title, items, unknown)
        payload["source_workout_date"] = source_date.isoformat()
        payload["source_workout_age_days"] = (today - source_date).days
        payload["routine_session_old"] = stale
        payload["database_sync_fresh"] = sync_fresh
        payload["history_may_be_stale"] = history_may_be_stale
        payload["source_workout_stale"] = history_may_be_stale
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
    if history_may_be_stale and not as_json:
        click.echo("Some exercise history may be stale.", err=True)


def _show_exercise_history(exercise: str, limit: int, db: Path) -> None:
    config = _config_for_database(None, db)
    _, policies = load_config(config)
    routines = load_routine_policies(config)
    with database(db) as connection:
        summaries = exercise_summaries(connection)
        stored = [item.title for item in summaries]
        exact_stored = next(
            (title for title in stored if title.casefold() == exercise.casefold()), None
        )
        policy_matches = [
            item
            for item in policies
            if exercise.casefold()
            in {
                item.name.casefold(),
                *(alias.casefold() for alias in item.aliases),
                *((item.display_name or "").casefold(),),
            }
        ]
        if exact_stored:
            policy = next((item for item in policies if _matches(exact_stored, item)), None)
            names = [exact_stored]
        elif len(policy_matches) == 1:
            policy = policy_matches[0]
            names = [
                title
                for title in stored
                if title.casefold()
                in {policy.name.casefold(), *(alias.casefold() for alias in policy.aliases)}
            ]
        elif len(policy_matches) > 1:
            choices = ", ".join(item.name for item in policy_matches)
            raise click.ClickException(
                f"Exercise name {exercise!r} is ambiguous. Use one of: {choices}"
            )
        else:
            candidates = sorted(
                {
                    *stored,
                    *(item.name for item in policies),
                    *(item.display_name for item in policies if item.display_name),
                }
            )
            suggestions = get_close_matches(exercise, candidates, n=3, cutoff=0.45)
            suffix = f" Did you mean {suggestions[0]!r}?" if suggestions else ""
            raise click.ClickException(f"No exact match for {exercise!r}.{suffix}")
        records = exercise_history(connection, names)
    if not records:
        raise click.ClickException(f"No history found for {exercise!r}.")
    sessions: dict[tuple[datetime, str], list] = {}
    for record in records:
        sessions.setdefault((record.started_at, record.routine), []).append(record)
    rows = []
    for (started_at, title), sets in list(sessions.items())[:limit]:
        routine = resolve_routine(title, routines)
        warmup_count = (
            routine.warmup_set_count(policy.name) if routine and policy is not None else 0
        )
        working = working_sets(sets, policy.sets if policy else None, warmup_count)
        warmups = [item for item in sets if item not in working]
        reps = sum(item.reps or 0 for item in working)
        weighted = [item for item in working if item.weight is not None and item.reps is not None]
        volume = sum(item.weight * item.reps for item in weighted) if weighted else None
        estimate = max((item.weight * (1 + item.reps / 30) for item in weighted), default=None)
        last_rpe = next((item.rpe for item in reversed(working) if item.rpe is not None), None)
        rows.append(
            (
                started_at,
                title,
                _format_logged_sets(warmups),
                _format_logged_sets(working),
                last_rpe,
                reps,
                volume,
                estimate,
            )
        )
    prior_volume = None
    output = []
    for started_at, title, warmup_text, working_text, last_rpe, reps, volume, estimate in reversed(
        rows
    ):
        change = "—" if prior_volume is None or volume is None else f"{volume - prior_volume:+g}"
        output.append(
            f"{as_local(started_at).strftime('%Y-%m-%d %H:%M')} · {title}\n"
            f"  Warm-up: {warmup_text}\n"
            f"  Working: {working_text}\n"
            f"  Last RPE: {last_rpe or '—'} · Reps: {reps} · "
            f"Volume: {volume if volume is not None else '—'} · "
            f"e1RM: {estimate if estimate is not None else '—'} · Change: {change}"
        )
        prior_volume = volume
    click.echo("\n\n".join(output))
    estimates = [row[-1] for row in rows if row[-1] is not None]
    if len(estimates) > 1:
        click.echo(
            f"Trend ({len(estimates)} sessions): estimated 1RM {estimates[0] - estimates[-1]:+g} lb."
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
    click.echo(f"ID    Date        {'Workout':{title_width}}  Duration  Exercises  Sets")
    for item in workouts:
        duration = "—" if item.duration_seconds is None else f"{item.duration_seconds // 60} min"
        click.echo(
            f"{item.id:<5} {local_date(item.started_at).isoformat()}  "
            f"{item.title:{title_width}}  {duration:8}  "
            f"{item.exercise_count:9}  {item.set_count:4}"
        )


def _workout_set_payload(item, classification: str) -> dict:
    return {
        "set": item.set_index + 1,
        "type": classification,
        "logged_type": item.set_type,
        "weight_lbs": item.weight,
        "reps": item.reps,
        "duration_seconds": item.duration_seconds,
        "rpe": item.rpe,
    }


@workout.command("show")
@click.argument("identifier")
@click.option("--json", "as_json", is_flag=True, help="emit structured JSON")
@_db_option
def workout_show(identifier: str, as_json: bool, db: Path) -> None:
    """Show one stored workout by ID, date, or title."""
    config = _config_for_database(None, db)
    _, policies = load_config(config)
    routines = load_routine_policies(config)
    with database(db) as connection:
        summaries = recent_workouts(connection, 100000)
        selected = (
            workout_summary_by_id(connection, int(identifier)) if identifier.isdigit() else None
        )
        if selected is None:
            matches = [
                item
                for item in summaries
                if local_date(item.started_at).isoformat() == identifier
                or _normalized(identifier) in _normalized(item.title)
            ]
            if not matches:
                raise click.ClickException(f"No stored workout matches {identifier!r}.")
            if len(matches) == 1:
                selected = matches[0]
            elif _is_interactive():
                labels = [
                    f"{item.id} · {as_local(item.started_at).strftime('%Y-%m-%d %H:%M')} · "
                    f"{item.title}"
                    for item in matches
                ]
                choice = choose_workout(labels, output=sys.stderr if as_json else None)
                if choice is None:
                    raise click.ClickException("Cancelled.")
                selected_id = int(choice.split(" · ", 1)[0])
                selected = next(item for item in matches if item.id == selected_id)
            else:
                choices = ", ".join(f"{item.id} ({item.title})" for item in matches)
                raise click.ClickException(f"Multiple workouts match. Use a workout ID: {choices}")
        details = workout_set_details(connection, selected.id)
    routine = resolve_routine(selected.title, routines)
    grouped: OrderedDict[tuple[int, str, int | None], list] = OrderedDict()
    for item in details:
        grouped.setdefault((item.exercise_order, item.exercise, item.superset_id), []).append(item)
    classified_groups = []
    for group, sets in grouped.items():
        policy = next((item for item in policies if _matches(group[1], item)), None)
        warmup_count = routine.warmup_set_count(policy.name) if routine and policy else 0
        working = working_sets(sets, policy.sets if policy else None, warmup_count)
        working_ids = {id(item) for item in working}
        classified_groups.append(
            (group, [(item, "working" if id(item) in working_ids else "warm-up") for item in sets])
        )
    if as_json:
        click.echo(
            json.dumps(
                {
                    "id": selected.id,
                    "title": selected.title,
                    "started_at": as_local(selected.started_at).isoformat(),
                    "duration_seconds": selected.duration_seconds,
                    "exercises": [
                        {
                            "exercise": exercise,
                            "superset_id": superset_id,
                            "sets": [
                                _workout_set_payload(item, classification)
                                for item, classification in sets
                            ],
                        }
                        for (_, exercise, superset_id), sets in classified_groups
                    ],
                },
                indent=2,
            )
        )
        return
    duration = (
        "—" if selected.duration_seconds is None else f"{selected.duration_seconds // 60} min"
    )
    blocks = [
        selected.title,
        f"ID: {selected.id}",
        f"When: {as_local(selected.started_at).strftime('%Y-%m-%d %H:%M %Z')}",
        f"Duration: {duration}",
    ]
    for (_, exercise, superset_id), sets in classified_groups:
        label = exercise + (f" · Superset {superset_id}" if superset_id is not None else "")
        lines = [label, "SET   TYPE      LOAD / RESULT      RPE"]
        for item, classification in sets:
            if item.duration_seconds is not None:
                result = f"{item.duration_seconds} sec"
            elif item.weight is None:
                result = f"{item.reps or '—'} reps"
            else:
                result = f"{item.weight:g} lb × {item.reps or '—'}"
            lines.append(
                f"{item.set_index + 1:<5} {classification:<9} {result:<18} "
                f"{item.rpe if item.rpe is not None else '—'}"
            )
        blocks.append("\n".join(lines))
    click.echo("\n\n".join(blocks))


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
            f"{local_date(item.last_started_at).isoformat()}  {item.set_count:10}"
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


@exercise.command("list")
@click.option("--search", help="filter exercise names")
@_db_option
def exercise_list(search: str | None, db: Path) -> None:
    """List exercise names that exist in the database."""
    with database(db) as connection:
        summaries = exercise_summaries(connection)
    if search:
        summaries = [item for item in summaries if _normalized(search) in _normalized(item.title)]
    if not summaries:
        raise click.ClickException("No matching exercises found.")
    click.echo("Exercise                              Sessions  Last done")
    for item in summaries:
        click.echo(
            f"{item.title[:36]:36}  {item.session_count:8}  "
            f"{local_date(item.last_started_at).isoformat()}"
        )


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
        latest_sync = connection.execute(
            "SELECT synced_at FROM sync_state WHERE provider = 'hevy_api'"
        ).fetchone()
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
                f"Latest workout: {local_date(latest_workout.started_at).isoformat()} — {latest_workout.title}",
            ]
        )
    if latest_import:
        lines.append(f"Latest import: {local_date(latest_import).isoformat()}")
    if latest_sync:
        synced_at = as_local(datetime.fromisoformat(latest_sync[0])).isoformat()
        lines.append(f"Latest API sync: {synced_at} ({timezone_name()})")
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
