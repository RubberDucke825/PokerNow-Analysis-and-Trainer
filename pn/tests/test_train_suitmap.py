"""Library flops dealt under a suit permutation (pn.train.suitmap): every trainer reader of the
flop document maps the actual cards into the document's suits, and the next street is lifted
back into actual cards."""

from __future__ import annotations

import random
from types import SimpleNamespace

from pn import ranges
from pn.solve.request import Seat, SpotRequest
from pn.train import postflop as post
from pn.train import session
from pn.train import suitmap as sm

C_H = (2, 1, 0, 3)  # clubs <-> hearts
DOC_BOARD = ("Kc", "9c", "8d")  # the library document's board
REAL_BOARD = ("Kh", "9h", "8d")  # the same board dealt under C_H


def _req(board: tuple[str, ...]) -> SpotRequest:
    seats = (Seat(None, "BB", 9000, hero=True, name="bb"), Seat(None, "BTN", 9000, name="btn"))
    return SpotRequest(seats=seats, street="flop", board=board, pot=2000, sb=50, bb=100)


def _doc() -> dict:
    """Flop document on DOC_BOARD: AcQc and AhQh play differently; EVs for loss_bb."""
    combos = ["AcQc", "AhQh", "JdTd"]
    return {"meta": {"ev_available": True}, "combos": combos, "root": "n0",
            "players": [{"position": "BB", "name": "bb"}, {"position": "BTN", "name": "btn"}],
            "nodes": {"n0": {
                "type": "action", "player": 0, "street": "flop",
                "actions": [{"token": "check", "child": "n1"}, {"token": "bet50", "child": "n2"}],
                "strategy": [[0.1, 0.9], [0.8, 0.2], [0.5, 0.5]],
                "action_ev": [[10.0, 40.0], [20.0, 5.0], [1.0, 1.0]],
                "summary": {"check": 0.45, "bet50": 0.55}}}}  # fmt: skip


def test_perm_helpers_round_trip() -> None:
    assert sm.inverse(C_H) == C_H and sm.inverse((1, 2, 3, 0)) == (3, 0, 1, 2)
    assert sm.permute_cards(DOC_BOARD, C_H) == REAL_BOARD
    assert sm.permute_combo("QhAh", sm.inverse(C_H)) == ranges.canonical_combo("AcQc")
    text = ranges.to_text({"AcQc": 1.0, "JdTd": 0.5})
    assert ranges.parse(sm.permute_range(text, C_H)) == ranges.parse(
        ranges.to_text({"AhQh": 1.0, "JdTd": 0.5}))


def test_permuted_hero_reads_like_the_canonical_combo() -> None:
    doc = _doc()
    canon = post.Tree(doc, "n0", _req(DOC_BOARD))
    dealt = post.Tree(doc, "n0", _req(REAL_BOARD), C_H)
    assert post.own_row(dealt, "AhQh") == post.own_row(canon, "AcQc") == [0.1, 0.9]
    assert post.own_row(canon, "AhQh") == [0.8, 0.2]  # the mapping matters
    assert post.strategy_row(dealt, "AhQh") == post.strategy_row(canon, "AcQc")
    assert post._ev_row(dealt, "AhQh", 100) == post._ev_row(canon, "AcQc", 100)
    for chosen in ("check", "bet50"):
        assert post.hero_grade(dealt, "AhQh", chosen, 100) == post.hero_grade(canon, "AcQc", chosen, 100)
    assert [post.villain_token(dealt, "AhQh", random.Random(k)) for k in range(20)] == [
        post.villain_token(canon, "AcQc", random.Random(k)) for k in range(20)]


def test_lifted_turn_is_in_actual_cards() -> None:
    dense = {"BB": ranges.to_dense({"AhAs": 1.0, "QsQd": 0.5}),
             "BTN": ranges.to_dense({"AcQc": 1.0, "JdTd": 0.5})}  # document suits
    doc = {"root": "n0", "combos": list(ranges.all_combos()),
           "players": [{"position": "BB", "name": "bb", "hero": True},
                       {"position": "BTN", "name": "btn", "hero": False}],
           "nodes": {"n0": {"type": "terminal", "kind": "leaf", "street": "flop", "pot": 2000,
                            "stacks": [9000, 9000],
                            "reach_by_player": [dense["BB"], dense["BTN"]]}}}  # fmt: skip
    hero = post.Player("BB", None, "bb", True, ("Ac", "As"))  # AhAs in the document
    h = SimpleNamespace(tree=post.Tree(doc, "n0", _req(REAL_BOARD), C_H),
                        players=(hero, post.Player("BTN", None, "btn", False, ("Jd", "Td"))))
    req = session._lift(h, ("2h",))
    assert req.street == "turn" and req.board == (*REAL_BOARD, "2h") and req.hero_cards == ("Ac", "As")
    got = {s.position: ranges.parse(s.range) for s in req.seats}
    assert set(got["BB"]) == {ranges.canonical_combo("AcAs"), ranges.canonical_combo("QsQd")}
    assert set(got["BTN"]) == {ranges.canonical_combo("AhQh"), ranges.canonical_combo("JdTd")}
