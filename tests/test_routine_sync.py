import io
import json
from unittest.mock import patch

import pytest

from hevy_coach.api_sync import KG_TO_LBS
from hevy_coach.gym_card import CardItem, CardSet
from hevy_coach.hevy_api import HevyAPI, HevyAPIError
from hevy_coach.models import ExercisePolicy, RoutinePolicy
from hevy_coach.routine_sync import compare_routine, comparison_json


class FakeAPI:
    def __init__(self, routine: dict, *, routines: list[dict] | None = None) -> None:
        self.saved = routine
        self.summaries = (
            routines if routines is not None else [{"id": routine["id"], "title": routine["title"]}]
        )
        self.templates = {
            exercise["exercise_template_id"]: {"title": exercise.pop("template_title")}
            for exercise in self.saved["exercises"]
            if "template_title" in exercise
        }

    def iter_routines(self):
        yield from self.summaries

    def routine(self, routine_id: str):
        assert routine_id == self.saved["id"]
        return self.saved

    def exercise_template(self, template_id: str):
        return self.templates[template_id]


def _policy(name: str, *aliases: str) -> ExercisePolicy:
    return ExercisePolicy(name, aliases, 3, 8, 10, 5)


def _routine(*exercises: dict) -> dict:
    return {
        "id": "routine-1",
        "title": "Strength A",
        "notes": "Keep this note",
        "folder_id": 7,
        "exercises": list(exercises),
    }


def _exercise(title: str, sets: list[dict], **extra) -> dict:
    return {
        "exercise_template_id": f"template-{title}",
        "template_title": title,
        "notes": "Keep exercise note",
        "rest_seconds": 90,
        "supersets_id": 2,
        "sets": sets,
        **extra,
    }


def _set(weight_lbs=None, reps=None, duration=None, set_type="normal") -> dict:
    return {
        "type": set_type,
        "weight_kg": None if weight_lbs is None else weight_lbs / KG_TO_LBS,
        "reps": reps,
        "distance_meters": None,
        "duration_seconds": duration,
        "custom_metric": None,
    }


def test_compare_prepares_update_and_preserves_unrelated_routine_fields() -> None:
    api = FakeAPI(
        _routine(
            _exercise("Dumbbell Bench Press", [_set(45, 10) for _ in range(3)]),
            _exercise("Unconfigured Movement", [_set(20, 8)]),
        )
    )
    policy = _policy("Bench Press (Dumbbell)", "Dumbbell Bench Press")
    configured = RoutinePolicy("Strength A", "Strength A", (policy.name,))
    item = CardItem(
        "Bench Press",
        50,
        "3×8",
        planned_sets=tuple(CardSet(index, 50, 8) for index in range(1, 4)),
        canonical_exercise=policy.name,
    )

    comparison = compare_routine(api, configured, [item], [policy])

    assert comparison.has_updates
    assert comparison.differences[0].exercise == "Bench Press"
    assert comparison.differences[0].current == "45×10×3"
    assert comparison.differences[0].recommended == "50×8×3"
    assert comparison.update_payload["notes"] == "Keep this note"
    assert comparison.update_payload["folder_id"] == 7
    assert comparison.update_payload["exercises"][0]["notes"] == "Keep exercise note"
    assert comparison.update_payload["exercises"][0]["superset_id"] == 2
    assert comparison.update_payload["exercises"][1]["sets"] == [_set(20, 8)]


def test_compare_handles_warmup_bodyweight_duration_and_rounding() -> None:
    api = FakeAPI(
        _routine(
            _exercise(
                "Dumbbell Bench Press",
                [_set(25.01, 8, set_type="warmup"), *[_set(50.01, 8) for _ in range(3)]],
            ),
            _exercise("Push Up", [_set(reps=12) for _ in range(3)]),
            _exercise("Plank", [_set(duration=60) for _ in range(3)]),
        )
    )
    policies = [
        _policy("Bench Press (Dumbbell)", "Dumbbell Bench Press"),
        _policy("Push Up"),
        _policy("Plank"),
    ]
    routine = RoutinePolicy("Strength A", "Strength A", tuple(item.name for item in policies))
    items = [
        CardItem(
            "Bench Press",
            50,
            "3×8",
            warmup="25 lb × 8",
            planned_sets=(
                CardSet(1, 25, 8),
                CardSet(2, 50, 8),
                CardSet(3, 50, 8),
                CardSet(4, 50, 8),
            ),
            canonical_exercise=policies[0].name,
        ),
        CardItem(
            "Push-Up",
            None,
            "3×12",
            planned_sets=tuple(CardSet(index, None, 12) for index in range(1, 4)),
            canonical_exercise="Push Up",
        ),
        CardItem(
            "Plank",
            None,
            "60s/60s/60s",
            planned_sets=tuple(CardSet(index, None, None, 60) for index in range(1, 4)),
            canonical_exercise="Plank",
        ),
    ]

    comparison = compare_routine(api, routine, items, policies)

    assert not comparison.differences
    assert comparison_json(comparison)["matches_gym_card"] is True


def test_zero_placeholders_are_not_displayed_as_zero_second_prescriptions() -> None:
    zero_set = {
        "type": "normal",
        "weight_kg": 0,
        "reps": 0,
        "duration_seconds": 0,
        "distance_meters": 0,
        "custom_metric": 0,
    }
    api = FakeAPI(_routine(_exercise("Dumbbell Bench Press", [zero_set.copy() for _ in range(3)])))
    policy = _policy("Bench Press (Dumbbell)", "Dumbbell Bench Press")
    configured = RoutinePolicy("Strength A", "Strength A", (policy.name,))
    item = CardItem(
        "Bench Press",
        50,
        "3×8",
        planned_sets=tuple(CardSet(index, 50, 8) for index in range(1, 4)),
        canonical_exercise=policy.name,
    )

    comparison = compare_routine(api, configured, [item], [policy])

    assert comparison.differences[0].current == "unprescribed×3"
    assert comparison.differences[0].recommended == "50×8×3"
    assert "0s" not in comparison.differences[0].current


def test_hevy_zero_duration_does_not_make_populated_weighted_sets_look_changed() -> None:
    sets = [_set(50, 8), *[_set(70, 10) for _ in range(3)]]
    for item in sets:
        item["duration_seconds"] = 0
    api = FakeAPI(_routine(_exercise("Machine Press", sets)))
    policy = _policy("Machine Press")
    configured = RoutinePolicy("Strength A", "Strength A", (policy.name,))
    item = CardItem(
        "Machine Press",
        70,
        "3×10",
        warmup="50 lb × 8",
        planned_sets=(
            CardSet(1, 50, 8),
            CardSet(2, 70, 10),
            CardSet(3, 70, 10),
            CardSet(4, 70, 10),
        ),
        canonical_exercise=policy.name,
    )

    comparison = compare_routine(api, configured, [item], [policy])

    assert not comparison.differences


def test_configured_warmup_does_not_create_drift_when_hevy_calls_it_normal() -> None:
    sets = [_set(30, 8), *[_set(50, 8) for _ in range(3)]]
    api = FakeAPI(_routine(_exercise("Dumbbell Bench Press", sets)))
    policy = _policy("Bench Press (Dumbbell)", "Dumbbell Bench Press")
    configured = RoutinePolicy("Strength A", "Strength A", (policy.name,))
    item = CardItem(
        "Bench Press",
        50,
        "3×8",
        warmup="30 lb × 8",
        planned_sets=(
            CardSet(1, 30, 8),
            CardSet(2, 50, 8),
            CardSet(3, 50, 8),
            CardSet(4, 50, 8),
        ),
        canonical_exercise=policy.name,
    )

    comparison = compare_routine(api, configured, [item], [policy])

    assert not comparison.differences
    assert comparison.update_payload["exercises"][0]["sets"][0]["type"] == "normal"


def test_preview_compacts_warmup_and_uneven_working_sets() -> None:
    api = FakeAPI(
        _routine(
            _exercise(
                "Dumbbell Bench Press",
                [_set(25, 8), _set(45, 10), _set(45, 9), _set(45, 8)],
            )
        )
    )
    policy = _policy("Bench Press (Dumbbell)", "Dumbbell Bench Press")
    configured = RoutinePolicy("Strength A", "Strength A", (policy.name,))
    item = CardItem(
        "Bench Press",
        50,
        "3×8",
        warmup="30 lb × 8",
        planned_sets=(
            CardSet(1, 30, 8),
            CardSet(2, 50, 8),
            CardSet(3, 50, 8),
            CardSet(4, 50, 8),
        ),
        canonical_exercise=policy.name,
    )

    difference = compare_routine(api, configured, [item], [policy]).differences[0]

    assert difference.current == "25×8 warm-up, 45×10, 45×9, 45×8"
    assert difference.recommended == "30×8 warm-up, 50×8×3"


def test_missing_exercise_is_reported_but_not_added() -> None:
    api = FakeAPI(_routine(_exercise("Dumbbell Bench Press", [_set(45, 8)])))
    policy = _policy("Cable Fly")
    configured = RoutinePolicy("Strength A", "Strength A", (policy.name,))
    item = CardItem(
        "Cable Fly",
        15,
        "3×10",
        planned_sets=tuple(CardSet(index, 15, 10) for index in range(1, 4)),
        canonical_exercise=policy.name,
    )

    comparison = compare_routine(api, configured, [item], [policy])

    assert not comparison.has_updates
    assert comparison.differences[0].updatable is False
    assert len(comparison.update_payload["exercises"]) == 1


def test_routine_matching_rejects_missing_and_ambiguous_matches() -> None:
    routine = _routine(_exercise("Dumbbell Bench Press", [_set(45, 8)]))
    configured = RoutinePolicy("Strength A", "Strength A", ())

    with pytest.raises(HevyAPIError, match="No saved"):
        compare_routine(FakeAPI(routine, routines=[]), configured, [], [])
    with pytest.raises(HevyAPIError, match="Multiple"):
        compare_routine(
            FakeAPI(
                routine,
                routines=[
                    {"id": "routine-1", "title": "Strength A"},
                    {"id": "routine-2", "title": "Strength-A"},
                ],
            ),
            configured,
            [],
            [],
        )


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def test_api_client_lists_gets_and_updates_routines() -> None:
    responses = [
        {"page": 1, "page_count": 1, "routines": [{"id": "routine 1", "title": "Strength A"}]},
        {"routine": {"id": "routine 1", "title": "Strength A", "exercises": []}},
        {"routine": {"id": "routine 1"}},
    ]
    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout))
        return Response(json.dumps(responses.pop(0)).encode())

    with patch("hevy_coach.hevy_api.urlopen", side_effect=open_request):
        api = HevyAPI("top-secret", base_url="https://example.test/v1")
        assert next(api.iter_routines())["id"] == "routine 1"
        assert api.routine("routine 1")["title"] == "Strength A"
        api.update_routine("routine 1", {"title": "Strength A", "exercises": []})

    put_request = requests[2][0]
    assert put_request.full_url.endswith("/routines/routine%201")
    assert put_request.method == "PUT"
    assert put_request.headers["Api-key"] == "top-secret"
    assert json.loads(put_request.data) == {"routine": {"title": "Strength A", "exercises": []}}
