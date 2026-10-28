import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from hevy_coach.api_sync import KG_TO_LBS
from hevy_coach.gym_card import CardItem, CardSet, build_card, card_json, render_card
from hevy_coach.hevy_api import HevyAPI, HevyAPIError
from hevy_coach.models import ExercisePolicy, RoutinePolicy, SetRecord
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
    configured = RoutinePolicy(
        "Strength A", "Strength A", (policy.name,), warmup_set_counts=((policy.name, 1),)
    )
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
    configured = RoutinePolicy(
        "Strength A", "Strength A", (policy.name,), warmup_set_counts=((policy.name, 1),)
    )
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


@pytest.mark.parametrize(
    "warmup_types", [("normal", "normal"), ("warmup", "normal"), ("warmup", "warmup")]
)
def test_card_preview_and_update_keep_all_configured_warmups(warmup_types) -> None:
    policy = _policy("Machine Press")
    routine = RoutinePolicy(
        "Strength A",
        "Strength A",
        (policy.name,),
        warmup_set_counts=((policy.name, 2),),
    )
    first = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        SetRecord(
            routine.title, first + timedelta(days=day), policy.name, index, kind, load, reps, 8
        )
        for day in (0, 3)
        for index, (kind, load, reps) in enumerate(
            [(warmup_types[0], 20, 8), (warmup_types[1], 40, 8), *[("normal", 60, 9)] * 3]
        )
    ]
    saved_sets = [
        _set(20, 8, set_type=warmup_types[0]),
        _set(40, 8, set_type=warmup_types[1]),
        *[_set(60, 9) for _ in range(3)],
    ]
    api = FakeAPI(_routine(_exercise(policy.name, saved_sets)))

    title, items = build_card(routine, routine.title, records, [policy])
    item = items[0]
    comparison = compare_routine(api, routine, items, [policy])
    updated_sets = comparison.update_payload["exercises"][0]["sets"]

    assert item.warmup_count == 2
    assert len(item.planned_sets) == 5
    assert [planned.number for planned in item.planned_sets] == [1, 2, 3, 4, 5]
    assert "20×8, 40×8" in render_card(title, items)
    assert "60×10×3" in render_card(title, items)
    assert len(card_json(title, items)["exercises"][0]["sets"]) == 5
    assert len(comparison.differences) == 1
    assert "20×8 warm-up, 40×8 warm-up" in comparison.differences[0].recommended
    assert updated_sets[:2] == saved_sets[:2]
    assert [planned["reps"] for planned in updated_sets[2:]] == [10, 10, 10]
    assert len(updated_sets) == len(saved_sets)

    api.saved["exercises"][0]["sets"] = updated_sets
    assert not compare_routine(api, routine, items, [policy]).differences


def test_explicit_warmups_are_kept_without_configured_count() -> None:
    policy = _policy("Machine Press")
    routine = RoutinePolicy("Strength A", "Strength A", (policy.name,))
    started = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        SetRecord(routine.title, started, policy.name, index, kind, weight, 8, 8)
        for index, (kind, weight) in enumerate(
            [("warmup", 20), ("warmup", 40), *[("normal", 60)] * 3]
        )
    ]

    _, items = build_card(routine, routine.title, records, [policy])

    assert items[0].warmup_count == 2
    assert [planned.weight for planned in items[0].planned_sets] == [20, 40, 60, 60, 60]


@pytest.mark.parametrize(
    ("progression", "configured_sets", "logged_sets", "warmups"),
    [
        ("weighted_reps", 3, 1, 2),
        ("weighted_reps", 4, 3, 1),
        ("bodyweight_reps", 3, 2, 0),
        ("duration", 3, 1, 0),
    ],
)
def test_incomplete_session_cannot_shorten_saved_routine(
    progression, configured_sets, logged_sets, warmups
) -> None:
    policy = replace(
        _policy("Movement"),
        progression=progression,
        duration_min_seconds=30,
        duration_max_seconds=60,
        duration_increment_seconds=5,
    )
    routine = RoutinePolicy(
        "Strength A",
        "Strength A",
        (policy.name,),
        warmup_set_counts=((policy.name, warmups),),
        working_set_counts=((policy.name, configured_sets),),
    )
    started = datetime(2026, 1, 1, tzinfo=UTC)
    weight = 60 if progression == "weighted_reps" else None
    reps = None if progression == "duration" else 8
    duration = 30 if progression == "duration" else None
    records = [
        SetRecord(
            routine.title,
            started,
            policy.name,
            index,
            "warmup" if index < warmups else "normal",
            20 if index < warmups else weight,
            8 if index < warmups else reps,
            8,
            duration_seconds=None if index < warmups else duration,
        )
        for index in range(warmups + logged_sets)
    ]
    saved_sets = [
        *[_set(20, 8, set_type="warmup") for _ in range(warmups)],
        *[_set(weight, reps, duration) for _ in range(configured_sets)],
    ]
    api = FakeAPI(_routine(_exercise(policy.name, saved_sets)))
    _, items = build_card(routine, routine.title, records, [policy])

    comparison = compare_routine(api, routine, items, [policy])

    assert not comparison.has_updates
    assert len(comparison.differences) == 1
    assert comparison.differences[0].updatable is False
    assert f"configuration requires {configured_sets}" in comparison.differences[0].recommended
    assert comparison.update_payload["exercises"][0]["sets"] == saved_sets


def test_full_exercise_can_update_while_incomplete_exercise_is_preserved() -> None:
    policies = [_policy("Movement A"), _policy("Movement B")]
    routine = RoutinePolicy("Strength A", "Strength A", tuple(item.name for item in policies))
    saved_sets = [_set(40, 8) for _ in range(3)]
    api = FakeAPI(
        _routine(
            _exercise("Movement A", saved_sets),
            _exercise("Movement B", saved_sets),
        )
    )
    items = [
        CardItem(
            policy.name,
            40,
            "",
            planned_sets=tuple(CardSet(index, 40, 9) for index in range(1, count + 1)),
            canonical_exercise=policy.name,
        )
        for policy, count in zip(policies, (1, 3), strict=True)
    ]

    comparison = compare_routine(api, routine, items, policies)

    assert comparison.has_updates
    assert [difference.updatable for difference in comparison.differences] == [False, True]
    assert comparison.update_payload["exercises"][0]["sets"] == saved_sets
    assert [item["reps"] for item in comparison.update_payload["exercises"][1]["sets"]] == [9] * 3


@pytest.mark.parametrize(("saved_count", "card_count"), [(2, 1), (1, 2), (2, 0), (0, 2)])
def test_different_warmup_counts_cannot_relabel_or_delete_working_sets(saved_count, card_count):
    policy = _policy("Movement")
    routine = RoutinePolicy(
        "Strength A",
        "Strength A",
        (policy.name,),
        warmup_set_counts=((policy.name, saved_count),),
    )
    saved_sets = [
        *[_set(20, 8, set_type="warmup") for _ in range(saved_count)],
        *[_set(40, 8) for _ in range(3)],
    ]
    api = FakeAPI(_routine(_exercise(policy.name, saved_sets)))
    item = CardItem(
        policy.name,
        40,
        "3×9",
        warmup="warm-ups" if card_count else None,
        planned_sets=tuple(
            [CardSet(index + 1, 20, 8) for index in range(card_count)]
            + [CardSet(card_count + index + 1, 40, 9) for index in range(3)]
        ),
        canonical_exercise=policy.name,
        warmup_set_count=card_count,
    )

    comparison = compare_routine(api, routine, [item], [policy])

    assert not comparison.has_updates
    assert comparison.differences[0].updatable is False
    assert "keeping saved exercise unchanged" in comparison.differences[0].recommended
    assert comparison.update_payload["exercises"][0]["sets"] == saved_sets


def test_set_types_are_matched_within_roles_when_warmups_are_interleaved():
    policy = _policy("Movement")
    routine = RoutinePolicy("Strength A", "Strength A", (policy.name,))
    api = FakeAPI(
        _routine(
            _exercise(
                policy.name,
                [
                    _set(40, 8, set_type="failure"),
                    _set(20, 8, set_type="warmup"),
                    _set(40, 8),
                    _set(40, 8, set_type="dropset"),
                ],
            )
        )
    )
    item = CardItem(
        policy.name,
        40,
        "3×9",
        warmup="20 lb × 8",
        planned_sets=(CardSet(1, 20, 8), CardSet(2, 40, 9), CardSet(3, 40, 9), CardSet(4, 40, 9)),
        canonical_exercise=policy.name,
        warmup_set_count=1,
    )

    comparison = compare_routine(api, routine, [item], [policy])

    assert comparison.has_updates
    assert [item["type"] for item in comparison.update_payload["exercises"][0]["sets"]] == [
        "warmup",
        "failure",
        "normal",
        "dropset",
    ]


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
    configured = RoutinePolicy(
        "Strength A", "Strength A", (policy.name,), warmup_set_counts=((policy.name, 1),)
    )
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
