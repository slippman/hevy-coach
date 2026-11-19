"""Compare logged exercise order with the currently configured routine."""

from dataclasses import dataclass

from .coach import _matches
from .models import ExercisePolicy, RoutinePolicy


@dataclass(frozen=True)
class OrderReview:
    expected_positions: tuple[int | None, ...]
    out_of_order: tuple[bool, ...]
    normalized_indices: tuple[int, ...]


def review_order(
    names: list[str], routine: RoutinePolicy | None, policies: list[ExercisePolicy]
) -> OrderReview:
    expected = {name: index + 1 for index, name in enumerate(routine.exercises)} if routine else {}
    positions = tuple(
        next(
            (
                expected[policy.name]
                for policy in policies
                if policy.name in expected and _matches(name, policy)
            ),
            None,
        )
        for name in names
    )
    # Only relative inversions count: omitted or unconfigured exercises do not
    # make the remaining exercises appear out of order.
    changed: set[int] = set()
    for index, position in enumerate(positions):
        if position is None:
            continue
        for later in range(index + 1, len(positions)):
            later_position = positions[later]
            if later_position is not None and position > later_position:
                changed.update((index, later))
    indices = tuple(
        sorted(
            range(len(names)),
            key=lambda index: positions[index] if positions[index] is not None else float("inf"),
        )
    )
    return OrderReview(positions, tuple(index in changed for index in range(len(names))), indices)
