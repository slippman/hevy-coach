import io
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from click.testing import CliRunner

from hevy_coach.api_sync import KG_TO_LBS, sync_workouts
from hevy_coach.cli import main
from hevy_coach.hevy_api import HevyAPI, HevyAPIError
from hevy_coach.importer import import_csv
from hevy_coach.storage import database


def workout_payload(
    *,
    workout_id: str = "workout-1",
    reps: int = 8,
    start_time: str = "2026-08-29T15:00:00Z",
) -> dict:
    return {
        "id": workout_id,
        "title": "Strength A",
        "description": "Synthetic API workout",
        "start_time": start_time,
        "end_time": "2026-08-29T16:00:00Z",
        "updated_at": "2026-08-29T16:01:00Z",
        "created_at": "2026-08-29T16:01:00Z",
        "exercises": [
            {
                "index": 0,
                "title": "Dumbbell Bench Press",
                "notes": "Synthetic fixture",
                "exercise_template_id": "template-1",
                "superset_id": None,
                "sets": [
                    {
                        "index": 0,
                        "type": "warmup",
                        "weight_kg": 25 / KG_TO_LBS,
                        "reps": 8,
                        "distance_meters": None,
                        "duration_seconds": None,
                        "rpe": 5,
                    },
                    {
                        "index": 1,
                        "type": "normal",
                        "weight_kg": 45 / KG_TO_LBS,
                        "reps": reps,
                        "distance_meters": 0,
                        "duration_seconds": None,
                        "rpe": 8,
                    },
                ],
            }
        ],
    }


class FakeSource:
    def __init__(self, events: list[dict], templates: dict[str, dict] | None = None) -> None:
        self.events = events
        self.templates = templates or {
            "template-1": {
                "id": "template-1",
                "title": "Dumbbell Bench Press",
                "type": "weight_reps",
            }
        }
        self.requested_since: list[str] = []

    def iter_workout_events(self, since: str):
        self.requested_since.append(since)
        yield from self.events

    def exercise_template(self, template_id: str) -> dict:
        return self.templates[template_id]


def test_sync_adds_updates_and_deletes_by_stable_hevy_id(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    now = datetime(2026, 8, 29, 18, tzinfo=UTC)
    with database(db) as connection:
        added = sync_workouts(
            connection,
            FakeSource([{"type": "updated", "workout": workout_payload()}]),
            now=now,
        )
        rows = connection.execute(
            "SELECT source_provider, source_id, duration_seconds, start_time FROM workouts"
        ).fetchall()
        sets = connection.execute(
            "SELECT set_type, weight_lbs, reps, distance_miles FROM sets ORDER BY set_index"
        ).fetchall()

    assert (added.workouts_added, added.workouts_updated, added.workouts_deleted) == (1, 0, 0)
    expected_start = datetime(2026, 8, 29, 15, tzinfo=UTC).isoformat()
    assert tuple(rows[0]) == ("hevy_api", "workout-1", 3600, expected_start)
    assert [tuple(row) for row in sets] == [
        ("warmup", 25, 8, None),
        ("normal", 45, 8, None),
    ]

    with database(db) as connection:
        updated = sync_workouts(
            connection,
            FakeSource([{"type": "updated", "workout": workout_payload(reps=10)}]),
            now=datetime(2026, 8, 29, 19, tzinfo=UTC),
        )
        counts = (
            connection.execute("SELECT COUNT(*) FROM workouts").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM sets").fetchone()[0],
        )
        latest_reps = connection.execute(
            "SELECT reps FROM sets WHERE set_type = 'normal'"
        ).fetchone()[0]

    assert updated.workouts_updated == 1
    assert counts == (1, 2)
    assert latest_reps == 10

    with database(db) as connection:
        deleted = sync_workouts(
            connection,
            FakeSource([{"type": "deleted", "id": "workout-1"}]),
            now=datetime(2026, 8, 29, 20, tzinfo=UTC),
        )
        workout_count = connection.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]

    assert deleted.workouts_deleted == 1
    assert workout_count == 0


def test_first_sync_uses_recent_local_history_and_then_saved_cursor(tmp_path: Path) -> None:
    db = tmp_path / "hevy.db"
    first_source = FakeSource([])
    with database(db) as connection:
        connection.execute(
            """INSERT INTO workouts
               (workout_key, title, start_time, created_at)
               VALUES ('local', 'Synthetic', '2026-08-20T12:00:00+00:00', 'now')"""
        )
        sync_workouts(
            connection,
            first_source,
            now=datetime(2026, 8, 29, 18, tzinfo=UTC),
        )

    assert first_source.requested_since == ["2026-08-19T12:00:00Z"]

    second_source = FakeSource([])
    with database(db) as connection:
        sync_workouts(
            connection,
            second_source,
            now=datetime(2026, 8, 30, 18, tzinfo=UTC),
        )

    assert second_source.requested_since == ["2026-08-29T18:00:00Z"]


def test_initial_api_sync_reconciles_csv_without_merging_distinct_same_day_session(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hevy.db"
    csv_path = tmp_path / "synthetic.csv"
    csv_path.write_text(
        "title,start_time,end_time,exercise_title,exercise_notes,set_index,set_type,weight_lbs,reps,rpe\n"
        "Strength A,2026-08-29 09:00:00,2026-08-29 10:00:00,Dumbbell Bench Press,Synthetic fixture,0,warmup,25,8,5\n"
        "Strength A,2026-08-29 09:00:00,2026-08-29 10:00:00,Dumbbell Bench Press,Synthetic fixture,1,normal,45,8,8\n"
        "Strength A,2026-08-29 12:00:00,2026-08-29 12:20:00,Dumbbell Bench Press,Other partial session,0,normal,45,6,9\n",
        encoding="utf-8",
    )
    with database(db) as connection:
        import_csv(connection, csv_path, tmp_path / "imports")
        result = sync_workouts(
            connection,
            FakeSource([{"type": "updated", "workout": workout_payload()}]),
            now=datetime(2026, 8, 29, 18, tzinfo=UTC),
        )
        rows = connection.execute("SELECT source_id FROM workouts").fetchall()

    assert result.workouts_added == 0
    assert result.workouts_updated == 1
    assert sorted((row["source_id"] or "csv") for row in rows) == ["csv", "workout-1"]


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_api_client_paginates_and_sends_key_without_exposing_it() -> None:
    pages = [
        {"page": 1, "page_count": 2, "events": [{"type": "deleted", "id": "one"}]},
        {"page": 2, "page_count": 2, "events": [{"type": "deleted", "id": "two"}]},
    ]
    requests = []

    def fake_open(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(json.dumps(pages.pop(0)).encode())

    with patch("hevy_coach.hevy_api.urlopen", side_effect=fake_open):
        events = list(HevyAPI("top-secret").iter_workout_events("2026-08-01T00:00:00Z"))

    assert [event["id"] for event in events] == ["one", "two"]
    assert requests[0][0].headers["Api-key"] == "top-secret"
    assert "top-secret" not in requests[0][0].full_url
    assert "page=2" in requests[1][0].full_url


def test_api_client_fetches_an_encoded_exercise_template_id() -> None:
    payload = {"id": "push/up", "title": "Push Up", "type": "bodyweight_reps"}
    with patch(
        "hevy_coach.hevy_api.urlopen",
        return_value=FakeResponse(json.dumps(payload).encode()),
    ) as opened:
        result = HevyAPI("top-secret").exercise_template("push/up")

    assert result == payload
    assert opened.call_args.args[0].full_url.endswith("/exercise_templates/push%2Fup")


def test_sync_preserves_modality_and_superset_and_normalizes_unused_zeroes(
    tmp_path: Path,
) -> None:
    workout = {
        "id": "bodyweight-1",
        "title": "BodyweightCircuit",
        "description": "",
        "start_time": "2026-01-15T10:00:00Z",
        "end_time": "2026-01-15T10:12:00Z",
        "exercises": [
            {
                "index": 0,
                "title": "Push Up",
                "notes": "",
                "exercise_template_id": "push",
                "superset_id": 0,
                "sets": [
                    {
                        "index": index,
                        "type": "normal",
                        "weight_kg": 0,
                        "reps": 10,
                        "distance_meters": 0,
                        "duration_seconds": 0,
                        "rpe": 7 if index == 2 else None,
                    }
                    for index in range(3)
                ],
            },
            {
                "index": 1,
                "title": "Pull Up",
                "notes": "",
                "exercise_template_id": "pull",
                "superset_id": 0,
                "sets": [
                    {
                        "index": index,
                        "type": "normal",
                        "weight_kg": 0,
                        "reps": reps,
                        "distance_meters": 0,
                        "duration_seconds": 0,
                        "rpe": 10 if index == 2 else None,
                    }
                    for index, reps in enumerate((3, 2, 2))
                ],
            },
            {
                "index": 2,
                "title": "Plank",
                "notes": "",
                "exercise_template_id": "plank",
                "superset_id": None,
                "sets": [
                    {
                        "index": index,
                        "type": "normal",
                        "weight_kg": 0,
                        "reps": 0,
                        "distance_meters": 0,
                        "duration_seconds": seconds,
                        "rpe": None,
                    }
                    for index, seconds in enumerate((50, 55, 50))
                ],
            },
        ],
    }
    templates = {
        "push": {"id": "push", "title": "Push Up", "type": "bodyweight_reps"},
        "pull": {"id": "pull", "title": "Pull Up", "type": "bodyweight_reps"},
        "plank": {"id": "plank", "title": "Plank", "type": "duration"},
    }
    db = tmp_path / "hevy.db"

    with database(db) as connection:
        sync_workouts(
            connection,
            FakeSource([{"type": "updated", "workout": workout}], templates),
            now=datetime(2026, 9, 10, 3, tzinfo=UTC),
        )
        exercises = connection.execute(
            """SELECT exercise_title, exercise_type, superset_id
               FROM exercises ORDER BY exercise_order"""
        ).fetchall()
        rows = connection.execute(
            """SELECT e.exercise_title, s.weight_lbs, s.reps, s.duration_seconds
               FROM sets s JOIN exercises e ON e.id = s.exercise_id
               ORDER BY e.exercise_order, s.set_index"""
        ).fetchall()

    assert [tuple(row) for row in exercises] == [
        ("Push Up", "bodyweight_reps", 0),
        ("Pull Up", "bodyweight_reps", 0),
        ("Plank", "duration", None),
    ]
    assert all(row["weight_lbs"] is None for row in rows)
    assert [row["reps"] for row in rows[:6]] == [10, 10, 10, 3, 2, 2]
    assert [tuple(row)[1:] for row in rows[6:]] == [
        (None, None, 50),
        (None, None, 55),
        (None, None, 50),
    ]


def test_api_client_gives_safe_authentication_error() -> None:
    error = HTTPError("https://api.hevyapp.com/v1/workouts/events", 403, "", {}, None)
    with (
        patch("hevy_coach.hevy_api.urlopen", side_effect=error),
        pytest.raises(HevyAPIError, match="rejected the API key") as caught,
    ):
        list(HevyAPI("top-secret").iter_workout_events("2026-08-01T00:00:00Z"))
    assert "top-secret" not in str(caught.value)


def test_api_client_wraps_direct_timeout_safely() -> None:
    with (
        patch("hevy_coach.hevy_api.urlopen", side_effect=TimeoutError("timed out")),
        pytest.raises(HevyAPIError, match="Could not reach the Hevy API: timed out"),
    ):
        HevyAPI("top-secret").exercise_template("template-1")


def test_sync_cli_requires_environment_key_and_reports_changes(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "hevy.db"

    missing = runner.invoke(main, ["sync", "--db", str(db)], env={"HEVY_API_KEY": ""})
    assert missing.exit_code == 1
    assert "HEVY_API_KEY is not set" in missing.output

    source = FakeSource([{"type": "updated", "workout": workout_payload()}])
    with patch("hevy_coach.cli.HevyAPI", return_value=source):
        synced = runner.invoke(
            main,
            ["sync", "--since", "2026-08-01", "--db", str(db)],
            env={"HEVY_API_KEY": "top-secret"},
        )

    assert synced.exit_code == 0, synced.output
    assert "1 new, 0 updated, 0 deleted" in synced.output
    assert "top-secret" not in synced.output

    status = runner.invoke(main, ["status", "--db", str(db)])
    assert "Latest API sync:" in status.output


def test_sync_cli_rejects_conflicting_history_options(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["sync", "--since", "2026-08-01", "--all", "--db", str(tmp_path / "hevy.db")],
        env={"HEVY_API_KEY": "top-secret"},
    )

    assert result.exit_code == 2
    assert "--since and --all cannot be combined" in result.output
