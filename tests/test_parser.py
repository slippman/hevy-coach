from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from hevy_coach.parser import HevyCSVError, filter_routines, filter_start_date, read_hevy_csv
from hevy_coach.time_utils import local_date

FIXTURE = Path(__file__).parent / "fixtures" / "current_workouts.csv"


def test_reads_hevy_export_and_identifies_warmups() -> None:
    records = read_hevy_csv(FIXTURE)

    assert len(records) == 25
    assert records[0].is_warmup
    assert not records[1].is_warmup
    assert records[1].weight == 50
    assert records[1].reps == 8


def test_filters_exact_routine_names_case_insensitively() -> None:
    records = filter_routines(read_hevy_csv(FIXTURE), ["strength a"])

    assert records
    assert {record.routine for record in records} == {"Strength A"}


def test_filters_out_workouts_before_start_date() -> None:
    records = filter_start_date(read_hevy_csv(FIXTURE), date(2024, 1, 10))

    assert all(local_date(record.started_at) >= date(2024, 1, 10) for record in records)
    assert {record.routine for record in records} == {"Strength-B", "Other Routine"}


def test_rejects_non_hevy_csv(tmp_path: Path) -> None:
    source = tmp_path / "bad.csv"
    source.write_text("foo,bar\n1,2\n", encoding="utf-8")

    with pytest.raises(HevyCSVError, match="Missing required"):
        read_hevy_csv(source)


def test_reads_hevys_display_style_date(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HEVY_TIMEZONE", "America/Denver")
    source = tmp_path / "export.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        'Strength A,"31 Jul 2026, 19:24",Dumbbell Bench Press,1,normal,45,8,8\n',
        encoding="utf-8",
    )

    records = read_hevy_csv(source)

    assert records[0].started_at == datetime(2026, 8, 1, 1, 24, tzinfo=UTC)
    assert local_date(records[0].started_at) == date(2026, 7, 31)
