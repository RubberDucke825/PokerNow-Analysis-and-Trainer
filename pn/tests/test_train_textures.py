"""Plan 14 P1: flop textures ("Spot importance - V3" Textures sheet) and their exact shares."""

from __future__ import annotations

import itertools

import pytest

from pn.train import textures as tx

DECK = [r + s for r in "23456789TJQKA" for s in "shdc"]
SHEET = {"Ace": 0.179, "Paired": 0.172, "Low": 0.152, "King": 0.149, "Queen": 0.122,
         "Jack": 0.098, "Ten": 0.076, "Monotone": 0.052}  # fmt: skip


@pytest.mark.parametrize(("board", "want"), [
    ("Ah8c3d", "Ace"), ("AhKcQd", "Ace"), ("Kd7s2c", "King"), ("QsJh4c", "Queen"),
    ("Jc9d2h", "Jack"), ("Td8c7h", "Ten"), ("9c6d5h", "Low"), ("4c3d2h", "Low"),
    ("7s7d2c", "Paired"), ("AsAd2c", "Paired"), ("2s2d2c", "Paired"),  # trips -> Paired
    ("AhKhQh", "Monotone"), ("7h5h2h", "Monotone"), ("Ts9s8s", "Monotone"),  # A-high monotone -> Monotone
    ("KdQd2c", "King"),  # two-tone stays its high card
])  # fmt: skip
def test_texture_classes(board: str, want: str) -> None:
    assert tx.texture(board) == want


def test_texture_reads_the_flop_of_a_longer_board_and_card_lists() -> None:
    assert tx.texture("Kd7s2c3h9d") == "King"
    assert tx.texture(["9c", "6d", "5h", "Ah"]) == "Low"  # only the flop counts
    with pytest.raises(ValueError):
        tx.texture("Kd7s")
    with pytest.raises(ValueError):
        tx.texture("KdKd2c")


def test_flop_share_is_exact_over_all_22100_flops() -> None:
    counts = dict.fromkeys(tx.TEXTURES, 0)
    for flop in itertools.combinations(DECK, 3):
        counts[tx.texture(flop)] += 1
    assert sum(counts.values()) == 22100
    assert counts == tx.FLOP_COUNT
    assert tx.FLOP_COUNT == {"Ace": 3960, "Paired": 3796, "Low": 3360, "King": 3300, "Queen": 2700,
                             "Jack": 2160, "Ten": 1680, "Monotone": 1144}  # fmt: skip
    assert sum(tx.FLOP_SHARE.values()) == pytest.approx(1.0, abs=1e-12)
    for t, share in tx.FLOP_SHARE.items():
        assert share == counts[t] / 22100
        assert share == pytest.approx(SHEET[t], abs=1e-3)  # the sheet rounds to 3 places
    assert list(tx.TEXTURES) == list(SHEET)  # the sheet's order (by frequency)
    assert set(tx.DETAILS) == set(tx.TEXTURES)


def test_boards_of_a_texture() -> None:
    boards = {"Kd7s2c": {"key": "a"}, "Ah8c3d": {"key": "b"}, "9c6d5h": {"key": "c"}}
    assert tx.boards_of(boards, "Low") == {"9c6d5h": {"key": "c"}}
    assert tx.boards_of(boards, "Paired") == {}
    assert tx.boards_of(boards, None) == boards
