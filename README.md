# Hevy Coach

Hevy Coach keeps a local workout history from the Hevy Pro API or CSV exports and turns the
latest session into a next-workout plan. The SQLite database—not an API response or periodically
overwritten CSV—is the source of truth.

## Releases

The current package version is `0.2.0`. We use [Semantic Versioning](https://semver.org/):
increment MAJOR for breaking command or data-format changes, MINOR for backward-compatible
features, and PATCH for backward-compatible fixes. Add every user-facing change under
`[Unreleased]` in [CHANGELOG.md](CHANGELOG.md); when releasing, move those entries into a dated
version section and update `version` in `pyproject.toml`.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd ~/dev/hevy-coach
uv sync --dev
```

## Sync with the Hevy Pro API

Hevy's [public API](https://api.hevyapp.com/docs/) is available to Pro subscribers. Get your key
from [Hevy's developer settings](https://hevy.com/settings?developer), expose it only to the
current terminal, and sync:

```bash
export HEVY_API_KEY="paste-your-key-here"
uv run hevy-coach sync
```

The key is read from the environment and is never written to the database, logs, or repository.
Avoid putting it directly in a Git-tracked file. On the first sync, Hevy Coach requests changes
beginning one day before the newest local workout; an empty database starts with the last 30 days.
Later runs use a saved cursor and retrieve only workout updates and deletions. Stable Hevy workout
IDs keep repeated syncs idempotent, and an equivalent existing CSV workout is reused when it can
be matched unambiguously.

Override the starting point or deliberately backfill the complete account history:

```bash
uv run hevy-coach sync --since 2026-08-01
uv run hevy-coach sync --all
```

`--all` can take longer because Hevy's workout-event pages contain at most ten items. API weights
are converted from kilograms to pounds and distances from meters to miles before storage so they
remain compatible with existing coaching configuration and CSV history. Exercise-template metadata
is cached locally as well. This lets the importer distinguish weighted, bodyweight, and duration
exercises and preserve Hevy's `superset_id` when the API supplies one; unused API zero values are
stored as missing values rather than invented `0 lb` loads or zero reps.

### Time zones

Workout timestamps are stored canonically in UTC. Hevy API timestamps already include an offset;
CSV timestamps do not, so Hevy Coach interprets CSV wall-clock values using `HEVY_TIMEZONE` before
converting them to UTC. Commands, reports, JSON dates, history, and gym-card freshness convert UTC
back to the computer's current local timezone. No configuration is normally required.

`HEVY_TIMEZONE` is an optional IANA-timezone override for imports or fixed-location output:

```bash
export HEVY_TIMEZONE="America/Denver"
```

When set, the override takes precedence over the system timezone. Database migration 3 converts
pre-existing CSV timestamps using the timezone active during migration, records that timezone in
the database, and regenerates workout and set keys while leaving already-correct API timestamps
unchanged.

## Import a Hevy export

```bash
uv run hevy-coach import ~/Downloads/hevy-export.csv
```

The command hashes the source with SHA-256, stores an import record, copies it immutably to
`data/imports/`, and inserts only previously unseen workouts, exercises, and sets. Re-running
the exact file (even under a different name) cleanly skips it. A newer full-history export is
safe: old rows are retained and only new rows are inserted. Imports use one SQLite transaction,
so a parsing or database failure cannot leave a partial data import.

Persistent data lives here:

- `data/hevy.db` — the long-term SQLite history.
- `data/imports/` — immutable source CSV archives.
- `data/reports/` — the newest and date-stamped Markdown reports.

Those mutable data files are ignored by Git by default.

## Reports and coaching

```bash
uv run hevy-coach report --latest
uv run hevy-coach report --latest --json
uv run hevy-coach report --latest --clipboard
```

Reports are generated only from the database, saved to `data/reports/latest.md` and a dated
file, and show warm-up/ramp-up sets, working sets, last-set RPE, and rules-based next-session
recommendations. `--clipboard` sends the Markdown to macOS `pbcopy`; `--json` writes the
structured report to standard output.

## Gym card

The terminal card uses Hevy-style rows: weighted work shows `SET / LBS / REPS`, bodyweight work
shows `SET / REPS`, and timed work shows `SET / SECONDS`. A configured ramp-up set appears first;
the rows that follow are the next working-set targets.

Generate a compact phone-friendly prescription from the latest session of a selected routine:

```bash
uv run hevy-coach gym-card
uv run hevy-coach gym-card --workout "Strength A"
uv run hevy-coach gym-card --workout "Strength B"
uv run hevy-coach gym-card --workout "Bodyweight Circuit"
uv run hevy-coach gym-card --workout "Strength A" --clipboard
uv run hevy-coach gym-card --json
uv run hevy-coach gym-card --all
```

With no workout option, the command presents configured strength routines in most-recent-first
order in an arrow-key selector; press Enter to choose, or Escape/Ctrl-C to cancel cleanly.
`--workout` accepts an exact stored title or a case-insensitive partial title. Multiple partial
matches show the same selector in a terminal, while non-interactive usage fails with a concise
list of matches. `--all` exposes older or unconfigured workout titles.

Default mode prints the card to standard output and never touches the clipboard. `--clipboard`
copies the card with `pbcopy`, prints no card, and confirms only on standard error. `--json`
emits structured JSON only. `--clipboard` cannot be combined with `--stdout` or `--json`.

Cards use only the selected workout's latest session, retain the configured exercise order,
include warm-ups only for exercises configured to require them, and treat early excess normal
sets as ramp-ups when the configured working-set count makes that unambiguous. The tracked
[`src/hevy_coach/default_config.toml`](src/hevy_coach/default_config.toml) contains generic examples.
Keep your real workout titles and routine details in `data/config.toml`; Hevy Coach loads that
private file automatically when using the default database, and Git ignores the entire `data/`
directory. Spacing and punctuation differences in workout titles match automatically, so routine
aliases are normally unnecessary.
For an exercise with only one logged session, cards repeat the logged working-set target as a
conservative baseline; normal double progression begins after the second session. Exercises not
in a known routine configuration are skipped and reported for review rather than added
automatically.

Each card includes the date of the stored workout it is based on. A source workout older than
seven calendar days shows a warning that the latest Hevy export may not have been imported; this
does not prevent card generation. Warm-ups are excluded only when Hevy marks them explicitly or
when the routine config specifies a warm-up count—load changes alone never create a warm-up.

For exports that label all ramp-up sets `normal`, the report conservatively treats early excess
sets as ramp-up sets when there are more normal sets than an exercise’s configured working-set
count. Explicit Hevy warm-up labels always take precedence.

## Workout and exercise history

```bash
uv run hevy-coach workout list
uv run hevy-coach workout history
uv run hevy-coach workout history --limit 15
uv run hevy-coach exercise history "Bench Press (Dumbbell)"
uv run hevy-coach exercise history "Bench Press (Dumbbell)" --limit 5
uv run hevy-coach status
```

`workout list` shows each unique workout title with its session count, most-recent date, and total
logged sets. `workout history` shows individual recent sessions with dates, titles, durations,
exercise counts, and set counts.
`exercise history` displays prior sessions for one exercise, including reps, last RPE, total reps,
and meaningful weighted volume. Distance- and duration-only work remains stored without invented
volume metrics.

`status` also shows the newest stored workout, most recent successful CSV import, and most recent
API sync, making it a quick check that your local history is current before generating a gym card.

## Backup and restore

Create a backup:

```bash
uv run hevy-coach backup ~/Documents/hevy-coach-backup.db
```

To restore, close any running Hevy Coach command and replace `data/hevy.db` with the backup
copy. Keep backups outside this repository or in your preferred encrypted backup service.

## Progression configuration

[`src/hevy_coach/default_config.toml`](src/hevy_coach/default_config.toml) shows the available
routine and exercise settings. Copy it to the ignored `data/config.toml` for everyday use, or pass
another file explicitly:

```bash
cp src/hevy_coach/default_config.toml data/config.toml
uv run hevy-coach report --config my-progression.toml
uv run hevy-coach gym-card --config my-progression.toml
```

Category defaults live under `[defaults.categories]`: compounds are 6–10, isolations are 8–10,
and core work is 8–12. An exercise inherits the global default, then its category, then any
exercise-specific override (highest precedence). For example:

```toml
[exercises."Lateral Raise (Dumbbell)"]
category = "isolation"
min_reps = 8
max_reps = 10
increment_lbs = 5
large_increment = true
```

You do not need to edit Python to change these preferences. Gym cards never prescribe more than
an exercise’s configured maximum. At the top of range, RPE ≤ 8.5 increases the configured load
and resets reps to the range minimum; RPE 9–9.5 repeats the ceiling. For a large percentage jump,
set `large_increment = true`. The engine then requires two consecutive successful ceiling sessions
(all target reps at last-set RPE ≤ 8.5) before increasing load. A missed ceiling or higher-RPE
session resets that confirmation streak. For example, the configured Lateral Raise repeats 10 lb
× 12/12/12 after its first clean ceiling session, then prescribes 15 lb × 8/8/8 after a second
consecutive clean ceiling session. RPE 10 retains the existing hold or reduce behavior.

Progression mode is exercise-specific. `weighted_reps` is the default; `bodyweight_reps` advances
reps without interpreting bodyweight as a zero-pound load. `duration` uses configurable seconds:

```toml
[exercises."Push Up"]
sets = 3
min_reps = 8
max_reps = 15
progression = "bodyweight_reps"

[exercises."Plank"]
sets = 3
progression = "duration"
min_seconds = 45
max_seconds = 60
increment_seconds = 5
```

The sample bodyweight routine coaches Push-Up, Pull-Up, and Plank while leaving cardio outside the
progression engine. It also shows how to configure a three-round superset with a rest window. On the
first logged session, the card repeats the actual baseline; normal progression starts only when
later history exists.

## Partial workouts

Every Hevy workout event remains a separate session, even when you do two parts of the same
routine on one day. A partial session updates progression only for exercises you actually logged;
skipped exercises are neither treated as failures nor reset. Gym cards retain the configured
routine order and use each exercise's most recent logged session, so a partial workout never makes
previously completed exercises disappear from the next card.

## Development

Install the development tools once:

```bash
uv sync --dev
```

Run the normal validation suite:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

`pytest` includes a terminal coverage report and fails below 70% coverage. To run the coverage
command explicitly:

```bash
uv run pytest --cov=hevy_coach --cov-report=term-missing --cov-fail-under=70
```

### Release checklist

- [ ] Sync the Hevy API or import the latest Hevy export locally.
- [ ] Run `uv run pytest` and verify coverage is at least 70%.
- [ ] Run `uv run ruff check .`.
- [ ] Run `uv run ruff format --check .`.
- [ ] Verify `uv run hevy-coach gym-card --workout "Strength A" --no-clipboard`.
- [ ] Verify `uv run hevy-coach gym-card --workout "Strength A" --clipboard` and confirm the clipboard.
- [ ] Move completed user-facing changes from `[Unreleased]` to a dated version in
  [CHANGELOG.md](CHANGELOG.md), then update `pyproject.toml` with the next semantic version.
