"""UTC storage and configured local-time presentation helpers."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zoneinfo(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown HEVY_TIMEZONE {name!r}") from error


def _system_timezone() -> tzinfo:
    environment_name = os.environ.get("TZ", "").strip().removeprefix(":")
    if environment_name and not environment_name.startswith("/"):
        try:
            return ZoneInfo(environment_name)
        except ZoneInfoNotFoundError:
            pass

    localtime = Path("/etc/localtime")
    try:
        resolved = str(localtime.resolve(strict=True))
    except OSError:
        resolved = ""
    marker = "/zoneinfo/"
    if marker in resolved:
        try:
            return ZoneInfo(resolved.split(marker, 1)[1])
        except ZoneInfoNotFoundError:
            pass

    detected = datetime.now().astimezone().tzinfo
    return detected or UTC


def local_timezone() -> tzinfo:
    """Return the current system timezone, unless explicitly overridden."""
    configured = os.environ.get("HEVY_TIMEZONE", "").strip()
    return _zoneinfo(configured) if configured else _system_timezone()


def timezone_name() -> str:
    """Return the active presentation timezone name."""
    timezone = local_timezone()
    return timezone.key if isinstance(timezone, ZoneInfo) else str(timezone)


def as_utc(value: datetime, *, naive_is_local: bool = False) -> datetime:
    """Normalize an aware timestamp, or an explicitly local naive value, to UTC."""
    if value.tzinfo is None:
        if not naive_is_local:
            raise ValueError("Timestamp must include a timezone")
        value = value.replace(tzinfo=local_timezone())
    return value.astimezone(UTC)


def as_local(value: datetime) -> datetime:
    """Convert a stored UTC timestamp for presentation."""
    return as_utc(value).astimezone(local_timezone())


def local_date(value: datetime) -> date:
    """Return the configured local calendar date for a stored timestamp."""
    return as_local(value).date()
