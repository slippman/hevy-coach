"""Terminal selection backed by questionary's maintained prompt widgets."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TextIO

import questionary


def choose_workout(options: Sequence[str], output: TextIO | None = None) -> str | None:
    """Return a selected title, or None when the prompt is cancelled."""
    return questionary.select(
        "Choose a workout:",
        choices=list(options),
        use_arrow_keys=True,
        use_jk_keys=False,
        use_search_filter=True,
        output=output,
    ).ask()
