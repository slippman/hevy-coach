from pathlib import Path

from hevy_coach.importer import file_sha256, import_csv, set_key, workout_key
from hevy_coach.parser import read_hevy_csv
from hevy_coach.storage import database

FIXTURE = Path(__file__).parent / "fixtures" / "current_workouts.csv"


def _import(db: Path, source: Path):
    with database(db) as connection:
        return import_csv(connection, source, db.parent / "imports")


def test_duplicate_and_same_contents_different_filename_are_skipped(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    first = _import(db, FIXTURE)
    duplicate = _import(db, FIXTURE)
    renamed = tmp_path / "renamed.csv"
    renamed.write_bytes(FIXTURE.read_bytes())
    same_contents = _import(db, renamed)

    assert first.workouts_added == 3
    assert first.sets_added == 25
    assert duplicate.skipped
    assert same_contents.skipped
    assert len(list((db.parent / "imports").iterdir())) == 1


def test_overlapping_export_adds_only_new_workout(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _import(db, FIXTURE)
    newer = tmp_path / "full-history-new.csv"
    newer.write_text(
        FIXTURE.read_text(encoding="utf-8")
        + "PF:Chest & Arms,2026-08-01 18:00:00,Dumbbell Bench Press,1,normal,45,8,8\n",
        encoding="utf-8",
    )

    result = _import(db, newer)

    assert not result.skipped
    assert result.workouts_added == 1
    assert result.sets_added == 1


def test_natural_keys_are_deterministic_and_sensitive_to_set_fields() -> None:
    records = read_hevy_csv(FIXTURE)

    assert workout_key(records[0]) == workout_key(records[1])
    assert set_key(workout_key(records[1]), records[1]) != set_key(
        workout_key(records[2]), records[2]
    )
    assert len(file_sha256(FIXTURE)) == 64


def test_migration_and_nullable_fields(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    _import(db, FIXTURE)
    with database(db) as connection:
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        nullable = connection.execute(
            "SELECT distance_miles, duration_seconds FROM sets LIMIT 1"
        ).fetchone()

    assert version == 1
    assert nullable[0] is None
    assert nullable[1] is None


def test_decimal_rpe_distance_and_duration_are_preserved(tmp_path: Path) -> None:
    source = tmp_path / "mixed.csv"
    source.write_text(
        "title,start_time,exercise_title,set_index,set_type,weight_lbs,reps,distance_miles,duration_seconds,rpe\n"
        "Cardio,2026-08-01 10:00:00,Treadmill,0,normal,,,1.25,600,9.5\n"
        "Strength,2026-08-01 11:00:00,Weighted Lift,0,normal,12.5,8,,,9.5\n",
        encoding="utf-8",
    )
    db = tmp_path / "hevy.db"

    result = _import(db, source)

    assert result.sets_added == 2
    with database(db) as connection:
        rows = connection.execute(
            "SELECT weight_lbs, distance_miles, duration_seconds, rpe FROM sets ORDER BY id"
        ).fetchall()
    assert tuple(rows[0]) == (None, 1.25, 600, 9.5)
    assert tuple(rows[1]) == (12.5, None, None, 9.5)
