# Contributor instructions

## Validation

Before finishing a change, run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Pytest enforces a minimum 70% coverage threshold. Add meaningful tests for changed behavior;
do not add tests solely to increase coverage.

## Personal data

Never commit personal Hevy exports, SQLite databases, import archives, reports, backups, or
other workout data. Keep mutable local data under `data/`, which is ignored by Git. Use clearly
synthetic fixtures for tests.

## Versions and documentation

- Follow Semantic Versioning for releases.
- Add user-facing changes to the `[Unreleased]` section of `CHANGELOG.md`.
- When releasing, move entries into a dated version section and update `version` in
  `pyproject.toml`.
- After committing a release, create and push an annotated `vMAJOR.MINOR.PATCH` tag that points
  to the release commit.
- Keep `README.md` accurate for setup, commands, and validation workflow.
- Add planned work and ideas to `ROADMAP.md`; move completed user-facing work to the changelog.
