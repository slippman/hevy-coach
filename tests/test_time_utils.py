from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from hevy_coach.time_utils import as_local, local_date, timezone_name


def test_output_timezone_follows_system_timezone_without_override(monkeypatch) -> None:
    monkeypatch.delenv("HEVY_TIMEZONE", raising=False)
    monkeypatch.setattr("hevy_coach.time_utils._system_timezone", lambda: ZoneInfo("Europe/London"))

    rendered = as_local(datetime(2026, 8, 29, 1, 28, tzinfo=UTC))

    assert rendered.isoformat() == "2026-08-29T02:28:00+01:00"
    assert timezone_name() == "Europe/London"


def test_timezone_override_changes_display_without_changing_utc_value(monkeypatch) -> None:
    stored = datetime(2026, 8, 29, 1, 28, tzinfo=UTC)

    monkeypatch.setenv("HEVY_TIMEZONE", "America/Denver")
    assert local_date(stored) == date(2026, 8, 28)

    monkeypatch.setenv("HEVY_TIMEZONE", "Asia/Tokyo")
    assert local_date(stored) == date(2026, 8, 29)
    assert stored == datetime(2026, 8, 29, 1, 28, tzinfo=UTC)
