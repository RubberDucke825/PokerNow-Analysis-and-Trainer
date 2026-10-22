"""Hand evaluator ordering and showdown strength classes."""

from __future__ import annotations

import pytest

from pn.profile.handeval import evaluate, strength_class
from pn.ranges import card_to_int


def _v(cards: str) -> int:
    return evaluate([card_to_int(c) for c in cards.split()])


def test_category_order() -> None:
    hands = [
        "As Ks Qs Js Ts 2h 3d",  # straight flush
        "9s 9h 9d 9c 2s 3h 4d",  # quads
        "9s 9h 9d 2c 2s 3h 4d",  # full house
        "As 9s 7s 4s 2s 3h Kd",  # flush
        "5s 6h 7d 8c 9s Kh 2d",  # straight
        "9s 9h 9d Kc 2s 3h 4d",  # trips
        "9s 9h Kd Kc 2s 3h 4d",  # two pair
        "9s 9h Kd Qc 2s 3h 4d",  # pair
        "As Jh 9d 7c 5s 3h 2d",  # high card
    ]
    values = [_v(h) for h in hands]
    assert values == sorted(values, reverse=True)


def test_wheel_and_kickers() -> None:
    assert _v("As 2h 3d 4c 5s Kh Qd") < _v("2s 3h 4d 5c 6s Kh Qd")
    assert _v("As Ah Kd 7c 5s 3h 2d") > _v("As Ah Qd 7c 5s 3h 2d")


@pytest.mark.parametrize(
    "hole,board,cls",
    [
        ("Ah Jh", "Jc Tc 4d 5h 2d", "top_pair"),
        ("Tc 9c", "Jc Tc 4d 5h 2d", "second_pair"),
        ("Qc Ks", "Jc Tc 4d 5h 2d", "air"),
        ("As Ad", "7c 7h 2d Kc 4s", "overpair"),
        ("Kh Qh", "7c 7h 2d Kc 4s", "top_pair"),
        ("Qs Qd", "Ks 8h 4c 2d 9s", "second_pair"),
        ("Qh Qd", "Qc 8c 5h 2s Ac", "set"),
        ("Kc Jc", "Qc 8c 5h 2s Ac", "flush"),
        ("7h 6h", "9h 5c 2c Jd 3s", "air"),
        ("8h 8c", "8d 8s 5h 2s 9c", "quads"),
        ("9h 2c", "9d 9s 5h Ks Qc", "trips"),
        ("Kh 9c", "Kd Ah 5h 5s 9h", "two_pair"),
        ("3h 3c", "Kd Ah 5h 7s 9h", "weak_pair"),
        ("2h 3c", "Ts Js Qs Ks As", "air"),  # plays the board
        ("6h 4c", "8d 7h 5h 2s Kc", "straight"),
    ],
)
def test_strength_class(hole: str, board: str, cls: str) -> None:
    assert strength_class(hole.split(), board.split()) == cls
