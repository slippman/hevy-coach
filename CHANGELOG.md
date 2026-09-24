# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Hevy Coach can now pull workouts directly from the Hevy Pro API. It remembers where the last
  sync stopped, so everyday updates stay quick.
- Gym cards now understand bodyweight exercises, timed exercises, and supersets. New exercises
  start conservatively from the first workout you log.
- You can inspect an individual workout by its stable history ID, browse stored exercise names,
  and ask a gym card for a short explanation of each recommendation.

### Changed

- Workout names and routine details can live in the private config beside your database instead
  of being baked into the project. Small spacing and punctuation differences match automatically.
- API sync keeps Hevy's exercise type and superset information. Bodyweight work no longer looks
  like a zero-pound weighted exercise, and timed sets stay measured in seconds.
- Split workouts remain separate sessions. Each exercise advances from the last time you actually
  performed it, so an unfinished workout does not count as a failure.
- Workout times are stored in UTC and shown in your computer's local time. CSV imports and API
  sync can arrive in either order without duplicating the same workout. Reconciliation also checks
  that timestamps are close, so repeated same-day sessions remain separate.
- Warm-ups come from Hevy's labels or your config rather than guesses based on a lighter first set.
- Gym cards show how fresh their source workout is and use the oldest exercise date when a routine
  was completed in parts. A recently synced database no longer warns that an older routine means
  data may be missing.
- Rep ranges, increments, and large weight jumps remain configurable. Large jumps now require two
  clean sessions at the top of the range before adding weight, and recommendations never go past
  an exercise's configured ceiling even when older logs did.
- Exercise history now separates warm-ups from working sets so warm-ups do not distort totals,
  volume, estimated strength, or trends.
- API sync keeps added weight on bodyweight exercises, and exercise history combines sessions
  logged under any configured name for the same movement.
- Max-effort bodyweight and timed sets now repeat the actual result instead of being raised to a
  configured minimum, and timed progress is reported as adding time rather than adding reps.
- First-session weighted targets preserve the actual baseline even below the configured range,
  Markdown reports display timed sets in seconds, and workout details retain distance results.
- A routine can explicitly use a different working-set count for one exercise without changing
  that exercise everywhere else or inferring a permanent change from an extra logged set.
- Gym cards now fit the workout into one compact table. Explanation mode puts a short, grouped
  coaching summary above it instead of adding a paragraph beneath every exercise.
- `status` now shows the latest workout, CSV import, and API sync.
- The old top-level `history` shortcut is gone; use `workout history` or `exercise history`.

## [0.2.0] - 2026-07-31

### Changed

- Enforced a 70% minimum test-coverage threshold in the normal pytest workflow.
- Documented the development validation suite and release checklist.
- Added `workout list` for unique workout titles and aggregate session counts.
- Reorganized history commands as `workout history` and `exercise history`.

## [0.1.0] - 2026-07-31

### Added

- Persistent SQLite workout history with idempotent Hevy CSV imports.
- Configurable workout and exercise progression policies.
- Coaching reports, history, status, backup, and restore commands.
- Interactive `gym-card` command with Hevy-style set, load, and rep rows.
- Optional macOS clipboard output and JSON gym-card output.
- Git protection for local workout data stored under `data/`.

[Unreleased]: https://github.com/slippman/hevy-coach/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/slippman/hevy-coach/releases/tag/v0.2.0
[0.1.0]: https://github.com/slippman/hevy-coach/releases/tag/v0.1.0
