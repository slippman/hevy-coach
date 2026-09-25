import sqlite3
from pathlib import Path

from hevy_coach.importer import file_sha256, import_csv, set_key, workout_key
from hevy_coach.parser import read_hevy_csv
from hevy_coach.storage import MIGRATIONS, database

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
        + "Strength A,2026-08-01 18:00:00,Dumbbell Bench Press,1,normal,45,8,8\n",
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

    assert version == 4
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


def test_legacy_csv_timestamps_migrate_to_utc_without_shifting_api(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HEVY_TIMEZONE", "America/Denver")
    db = tmp_path / "hevy.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for version, sql in enumerate(MIGRATIONS, start=1):
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES (?, 'legacy')", (version,)
        )
    connection.execute(
        """INSERT INTO workouts
           (workout_key, title, start_time, end_time, description, duration_seconds, created_at,
            source_provider, source_id)
           VALUES ('legacy-workout', 'Synthetic', '2026-01-15T18:00:00+00:00',
                   '2026-01-15T19:00:00+00:00', '', 3600, 'legacy', 'csv', NULL)"""
    )
    csv_workout_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        """INSERT INTO exercises
           (workout_id, exercise_title, exercise_notes, exercise_order)
           VALUES (?, 'Synthetic Lift', '', 0)""",
        (csv_workout_id,),
    )
    exercise_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        """INSERT INTO sets
           (set_key, exercise_id, set_index, set_type, weight_lbs, reps,
            distance_miles, duration_seconds, rpe)
           VALUES ('legacy-set', ?, 0, 'normal', 45, 8, NULL, NULL, 8)""",
        (exercise_id,),
    )
    connection.execute(
        """INSERT INTO workouts
           (workout_key, title, start_time, end_time, description, duration_seconds, created_at,
            source_provider, source_id)
           VALUES ('api-workout', 'API Synthetic', '2026-08-29T01:28:23+00:00',
                   '2026-08-29T01:46:43+00:00', '', 1100, 'legacy', 'hevy_api', 'api-1')"""
    )
    connection.execute(
        """INSERT INTO workouts
           (workout_key, title, start_time, end_time, description, duration_seconds, created_at,
            source_provider, source_id)
           VALUES ('offset-workout', 'Offset Synthetic', '2026-01-15T18:00:00-05:00',
                   '2026-01-15T19:00:00-05:00', '', 3600, 'legacy', 'csv', NULL)"""
    )
    connection.commit()
    connection.close()

    source = tmp_path / "same-workout.csv"
    source.write_text(
        "title,start_time,end_time,exercise_title,set_index,set_type,weight_lbs,reps,rpe\n"
        "Synthetic,2026-01-15 18:00:00,2026-01-15 19:00:00,Synthetic Lift,0,normal,45,8,8\n",
        encoding="utf-8",
    )
    with database(db) as migrated:
        rows = migrated.execute(
            "SELECT source_provider, start_time, end_time FROM workouts ORDER BY id"
        ).fetchall()
        keys = migrated.execute(
            "SELECT workout_key, (SELECT set_key FROM sets LIMIT 1) FROM workouts WHERE id = ?",
            (csv_workout_id,),
        ).fetchone()
        timezone = migrated.execute(
            "SELECT value FROM app_metadata WHERE key = 'legacy_csv_timezone'"
        ).fetchone()[0]
        result = import_csv(migrated, source, tmp_path / "imports")

    assert tuple(rows[0]) == (
        "csv",
        "2026-01-16T01:00:00+00:00",
        "2026-01-16T02:00:00+00:00",
    )
    assert tuple(rows[1]) == (
        "hevy_api",
        "2026-08-29T01:28:23+00:00",
        "2026-08-29T01:46:43+00:00",
    )
    assert tuple(rows[2]) == (
        "csv",
        "2026-01-15T23:00:00+00:00",
        "2026-01-16T00:00:00+00:00",
    )
    assert keys[0] != "legacy-workout"
    assert keys[1] != "legacy-set"
    assert timezone == "America/Denver"
    assert result.workouts_added == 0
    assert result.sets_added == 0
