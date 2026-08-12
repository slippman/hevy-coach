# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Removed the legacy top-level `history` command; use `workout history` or `exercise history`.
- Added an alias for Hevy's `PF:Back& Arms` title so it uses the Back & Arms gym-card policy.
- Configured the first PF: Back & Arms baseline, including conservative first-session gym cards
  and review warnings for unknown routine exercises.

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
