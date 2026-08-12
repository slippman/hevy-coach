# Hevy Coach

Hevy Coach keeps a local, permanent workout history from Hevy CSV exports and turns the latest
session into a next-workout plan. The SQLite database—not the periodically overwritten CSV—is
the source of truth.

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

The terminal card uses Hevy-style `SET / LBS / REPS` rows. A configured ramp-up
set appears first; the rows that follow are the next working-set targets.

Generate a compact phone-friendly prescription from the latest session of a selected routine:

```bash
uv run hevy-coach gym-card
uv run hevy-coach gym-card --workout "PF:Chest & Arms"
uv run hevy-coach gym-card --workout "PF: Back & Arms"
uv run hevy-coach gym-card --workout chest --clipboard
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
sets as ramp-ups when the configured working-set count makes that unambiguous. Routine display
names, exercise order, aliases, rep ranges, increments, warm-up behavior, and short canonical
names live in
[`src/hevy_coach/default_config.toml`](src/hevy_coach/default_config.toml).
Routine aliases there allow minor Hevy title spelling changes to use the same gym-card policy.
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

`status` also shows the newest stored workout and the most recent successful import date, making
it a quick check that your local history is current before generating a gym card.

## Backup and restore

Create a backup:

```bash
uv run hevy-coach backup ~/Documents/hevy-coach-backup.db
```

To restore, close any running Hevy Coach command and replace `data/hevy.db` with the backup
copy. Keep backups outside this repository or in your preferred encrypted backup service.

## Progression configuration

[`src/hevy_coach/default_config.toml`](src/hevy_coach/default_config.toml) defines canonical
exercise names, aliases, working-set counts, rep ranges, increments, and optional starting
weights. Copy it, edit it, then pass it to reporting:

```bash
uv run hevy-coach report --config my-progression.toml
```

The default rules increase weight when every working set reaches the rep-range ceiling at RPE
8.5 or lower; hold and add reps when in range but not yet at the ceiling; and never increase at
an RPE of 10. High RPE and a rep drop below range cause a hold/reduction recommendation.

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

- [ ] Import the latest Hevy export locally.
- [ ] Run `uv run pytest` and verify coverage is at least 70%.
- [ ] Run `uv run ruff check .`.
- [ ] Run `uv run ruff format --check .`.
- [ ] Verify `uv run hevy-coach gym-card --workout chest --no-clipboard`.
- [ ] Verify `uv run hevy-coach gym-card --workout chest --clipboard` and confirm the clipboard.
- [ ] Move completed user-facing changes from `[Unreleased]` to a dated version in
  [CHANGELOG.md](CHANGELOG.md), then update `pyproject.toml` with the next semantic version.
