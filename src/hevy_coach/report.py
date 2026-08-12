from __future__ import annotations

from datetime import date

from .coach import summarize_workouts
from .models import Recommendation, SetRecord


def markdown_report(
    records: list[SetRecord],
    recommendations: list[Recommendation],
    routines: list[str],
    start_date: date,
) -> str:
    count, latest = summarize_workouts(records)
    lines = [
        "# Hevy Coach — Next Session",
        "",
        f"Analyzed **{count} workout{'s' if count != 1 else ''}** from **{start_date.isoformat()}** through **{latest}**.",
        f"Routines: {', '.join(f'`{routine}`' for routine in routines)}",
        "",
        "Warm-up sets are excluded. RPE is taken from the last working set with a logged RPE.",
        "",
        "| Exercise | Action | Next time | Evidence |",
        "|---|---|---|---|",
    ]
    for item in recommendations:
        values = (item.exercise, item.action.value, item.message, item.evidence)
        escaped = [value.replace("|", "\\|") for value in values]
        lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines) + "\n"
