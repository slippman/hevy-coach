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

- Keep personal workout titles and routine definitions only in `data/config.toml` or another
  ignored local configuration file.
- Tracked defaults, documentation, and tests must use clearly generic routine names and synthetic
  workout data.
- Do not add tracked aliases or special cases for one person's workout titles. Handle harmless
  spacing and punctuation differences generically.
- Before pushing, inspect both the final files and every commit added by the branch for personal
  data or credentials.
- If personal data entered an unmerged branch's history, rewrite the branch before review.

## Writing style

- Write changelog entries and pull-request descriptions in plain, conversational language.
- Explain what changed and why it matters to the person using the project.
- Avoid dense feature inventories, implementation jargon, and internal class or schema names unless
  they are necessary to understand the change.
- Never mention personal workout or routine names in commits, changelogs, pull-request titles, or
  pull-request descriptions.

## Versions and documentation

- Follow Semantic Versioning for releases.
- Add user-facing changes to the `[Unreleased]` section of `CHANGELOG.md`.
- When releasing, move entries into a dated version section and update `version` in
  `pyproject.toml`.
- After committing a release, create and push an annotated `vMAJOR.MINOR.PATCH` tag that points
  to the release commit.
- Keep `README.md` accurate for setup, commands, and validation workflow.
- Add planned work and ideas to `ROADMAP.md`; move completed user-facing work to the changelog.
