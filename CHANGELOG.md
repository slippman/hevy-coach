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

### Changed

- Workout names and routine details can live in the private config beside your database instead
  of being baked into the project. Small spacing and punctuation differences match automatically.
- API sync keeps Hevy's exercise type and superset information. Bodyweight work no longer looks
  like a zero-pound weighted exercise, and timed sets stay measured in seconds.
- Split workouts remain separate sessions. Each exercise advances from the last time you actually
  performed it, so an unfinished workout does not count as a failure.
- Workout times are stored in UTC and shown in your computer's local time. Existing CSV imports
  continue to work and can be matched with the same workout later returned by the API.
- Warm-ups come from Hevy's labels or your config rather than guesses based on a lighter first set.
- Gym cards show how fresh their source workout is and use the oldest exercise date when a routine
  was completed in parts.
- Rep ranges, increments, and large weight jumps remain configurable. Large jumps now require two
  clean sessions at the top of the range before adding weight.
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
