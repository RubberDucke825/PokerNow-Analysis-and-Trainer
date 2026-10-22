"""pn.train.engine (betting state) and pn.train.showdown (side pots, settlement)."""

from __future__ import annotations

import pytest

from pn.train.engine import (
    IllegalAction,
    apply,
    hand_decided,
    next_street,
    raise_cap,
    round_closed,
    start,
)
from pn.train.showdown import settle, side_pots

SIX = ("LJ", "HJ", "CO", "BTN", "SB", "BB")


def _st(stack: int = 10_000):
    return start(SIX, stack, 50, 100)


def test_start_posts_blinds_and_lj_acts_first() -> None:
    st = _st()
    assert st.pot == 150 and st.committed == (0, 0, 0, 0, 50, 100)
    assert st.positions[st.to_act] == "LJ" and st.to_call(st.to_act) == 100


def test_fold_around_to_bb_ends_the_hand() -> None:
    st = _st()
    for _ in range(5):
        st = apply(st, "fold")
    assert hand_decided(st) and st.in_hand() == [5]


def test_bb_gets_the_option_after_limps() -> None:
    st = apply(_st(), "call")  # LJ limps
    for _ in range(3):
        st = apply(st, "fold")
    st = apply(st, "call")  # SB completes
    assert st.positions[st.to_act] == "BB" and st.to_call(st.to_act) == 0
    st = apply(st, "check")
    assert round_closed(st) and st.pot == 300


def test_raise_call_then_postflop_order() -> None:
    st = apply(_st(), "raise", 250)
    for _ in range(4):
        st = apply(st, "fold")
    st = apply(st, "call")  # BB
    assert round_closed(st) and st.pot == 550
    flop = next_street(st, ("Kd", "7s", "2c"))
    assert flop.street == "flop" and flop.committed == (0,) * 6
    assert flop.positions[flop.to_act] == "BB"  # BB acts first postflop, LJ last
    flop = apply(apply(flop, "check"), "check")
    assert round_closed(flop) and not hand_decided(flop)


def test_illegal_actions() -> None:
    st = _st()
    with pytest.raises(IllegalAction):
        apply(st, "check")  # facing the big blind
    with pytest.raises(IllegalAction):
        apply(st, "raise", 150)  # below the minimum raise to 200
    st = apply(st, "raise", 300)
    with pytest.raises(IllegalAction):
        apply(st, "raise", 400)  # min re-raise is to 500


def test_allin_is_capped_at_the_deepest_other_stack() -> None:
    st = start(("SB", "BB"), 5_000, 50, 100)
    st = apply(st, "raise", 300)
    deep = start(("SB", "BB"), 5_000, 50, 100)
    assert deep.max_to(0) == 5_000
    st2 = apply(st, "allin")  # BB jams
    assert st2.committed[1] == 5_000 and st2.stacks[1] == 0
    st3 = apply(st2, "call")
    assert hand_decided(st3) and st3.pot == 10_000


def test_raise_cap_converts_big_raises_to_allin() -> None:
    st = start(("SB", "BB"), 1_000, 50, 100)
    assert raise_cap(st, 0, 300) == ("raise", 300)
    assert raise_cap(st, 0, 800) == ("allin", 1_000)  # commits > 67% of the stack
    assert raise_cap(st, 0, 120)[1] == 200  # lifted to the minimum raise


def test_side_pots_with_dead_money() -> None:
    # A all-in 100, B 300, C 300; D folded after putting in 50.
    pots = side_pots([100, 300, 300, 50], [False, False, False, True])
    assert pots == [(350, [0, 1, 2]), (400, [1, 2])]
    assert sum(a for a, _ in pots) == 750


def test_settle_best_hand_wins_each_pot() -> None:
    board = ["2c", "7d", "9h", "Js", "3s"]
    holes = [["Ah", "Ad"], ["Kh", "Kd"], ["Qh", "Qd"]]
    won = settle([100, 300, 300], [False, False, False], holes, board)
    assert won == [300, 400, 0]  # AA takes the main pot, KK the side pot


def test_settle_split_and_fold_out() -> None:
    board = ["Ac", "Kd", "Qh", "Js", "Ts"]  # broadway on board: everyone splits
    won = settle([200, 200, 100], [False, False, True], [["2c", "3d"], ["4c", "5d"], ["6c", "7d"]], board)
    assert won == [250, 250, 0]
    assert settle([200, 100], [False, True], [["2c", "3d"], ["4c", "5d"]], board) == [300, 0]
