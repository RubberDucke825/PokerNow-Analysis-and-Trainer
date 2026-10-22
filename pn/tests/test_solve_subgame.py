"""pn.solve.subgame on hand-built strategy documents (contracts/strategy_output.md with
reach_by_player) and pn.solve.ev.action_ev_loss."""

from __future__ import annotations

import copy

import pytest

from pn import ranges
from pn.live.assist import ev_loss_for
from pn.solve import EvUnavailable, Seat, SpotRequest, SubgameError, action_ev_loss, subgame

COMBOS = list(ranges.all_combos())
IDX = {c: i for i, c in enumerate(COMBOS)}


def _dense(weights: dict[str, float]) -> list[float]:
    out = [0.0] * len(COMBOS)
    for c, w in weights.items():
        out[IDX[ranges.canonical_combo(c)]] = w
    return out


BB_R = {"AsAh": 0.5, "8h8d": 0.5, "QcJc": 0.25}  # 8h8d dies on an 8h turn
CO_R = {"AdKc": 0.8, "QsQh": 0.4}
BTN_R = {"9s9c": 1.0}
FLOP = ("Kd", "7s", "2c")


def _flop_doc(*, leaf_fields: bool = True) -> dict:
    """3-way flop: BB checks, CO bets 50%, BTN folds, BB calls -> leaf."""
    reach = [_dense(BB_R), _dense(CO_R), _dense(BTN_R)]
    call_strat = [[0.0, 0.0] for _ in COMBOS]  # BB at n3: [fold, call]
    for c in BB_R:
        call_strat[IDX[c]] = [0.5, 0.5]
    leaf_reach = [[w * 0.5 for w in reach[0]], reach[1], reach[2]]
    leaf = {"type": "terminal", "kind": "leaf", "pot": 2000}
    if leaf_fields:
        leaf |= {"street": "flop", "stacks": [9200, 9200, 9700], "reach_by_player": leaf_reach}
    return {
        "meta": {"backend": "mwsolver", "mode": "depth_limited"},
        "players": [{"index": 0, "name": "bb", "position": "BB", "hero": False},
                    {"index": 1, "name": "co", "position": "CO", "hero": True},
                    {"index": 2, "name": "btn", "position": "BTN", "hero": False}],
        "combos": COMBOS, "root": "n0",
        "nodes": {
            "n0": {"type": "action", "street": "flop", "player": 0, "pot": 1000,
                   "stacks": [9700, 9700, 9700], "actions": [{"token": "check", "child": "n1"}],
                   "reach_by_player": reach},
            "n1": {"type": "action", "street": "flop", "player": 1, "pot": 1000,
                   "stacks": [9700, 9700, 9700],
                   "actions": [{"token": "bet50", "amount": 500, "child": "n2"}],
                   "reach_by_player": reach},
            "n2": {"type": "action", "street": "flop", "player": 2, "pot": 1500,
                   "stacks": [9700, 9200, 9700], "actions": [{"token": "fold", "child": "n3"}],
                   "reach_by_player": reach},
            "n3": {"type": "action", "street": "flop", "player": 0, "pot": 1500,
                   "stacks": [9700, 9200, 9700], "reach_by_player": reach, "strategy": call_strat,
                   "actions": [{"token": "fold", "child": "n5"}, {"token": "call", "child": "n4"}]},
            "n4": leaf,
            "n5": {"type": "terminal", "kind": "fold", "pot": 1500},
        },
    }  # fmt: skip


def _hint() -> SpotRequest:
    seats = (Seat("pBB", "BB", 9700), Seat("pCO", "CO", 9700, hero=True), Seat("pBTN", "BTN", 9700))
    return SpotRequest(seats=seats, street="flop", board=FLOP, pot=1000, sb=50, bb=100,
                       last_aggressor="CO")  # fmt: skip


def _range(spot: dict, pos: str) -> dict[str, float]:
    return ranges.parse(next(p for p in spot["players"] if p["position"] == pos)["range"])


@pytest.mark.parametrize("leaf_fields", [True, False])
def test_flop_leaf_to_turn(leaf_fields: bool) -> None:
    spot = subgame(_flop_doc(leaf_fields=leaf_fields), "n4", ("8h",), _hint())
    g = spot["game"]
    assert g["street"] == "turn" and g["board"] == [*FLOP, "8h"] and g["pot"] == 2000
    assert [p["position"] for p in spot["players"]] == ["BB", "CO"]  # BTN folded
    assert [p["stack"] for p in spot["players"]] == [9200, 9200]
    assert g["last_aggressor"] == "CO"
    bb, co = _range(spot, "BB"), _range(spot, "CO")
    assert bb == {"AsAh": 1.0, "QcJc": 0.5}  # 8h8d dead; scaled to max 1
    assert co == {"AdKc": 1.0, "QsQh": 0.5}
    assert spot["players"][1]["hero"] is True
    assert all("lock" not in p for p in spot["players"])  # GTO: no villain locks


def test_leaf_with_null_row_for_folded_player() -> None:
    doc = _flop_doc()
    leaf = doc["nodes"]["n4"]
    leaf["reach_by_player"] = [leaf["reach_by_player"][0], leaf["reach_by_player"][1], None]
    spot = subgame(doc, "n4", ("8h",), _hint())
    assert [p["position"] for p in spot["players"]] == ["BB", "CO"]


def _sparse_doc() -> dict:
    """reach_by_player only at the root (a hero-style node); later nodes carry strategy."""
    doc = _flop_doc(leaf_fields=False)
    ones = [[1.0] for _ in COMBOS]
    for nid in ("n1", "n2", "n3"):
        del doc["nodes"][nid]["reach_by_player"]
    doc["nodes"]["n0"]["strategy"] = ones  # BB checks
    doc["nodes"]["n1"]["strategy"] = ones  # CO bets
    doc["nodes"]["n2"]["strategy"] = ones  # BTN folds
    return doc


def test_reach_derived_from_nearest_ancestor_with_rows() -> None:
    spot = subgame(_sparse_doc(), "n4", ("8h",), _hint())
    assert [p["stack"] for p in spot["players"]] == [9200, 9200]
    assert _range(spot, "BB") == {"AsAh": 1.0, "QcJc": 0.5}  # x 0.5 call at n3, scaled to max 1
    assert _range(spot, "CO") == {"AdKc": 1.0, "QsQh": 0.5}


def test_missing_strategy_on_the_way_is_a_clear_error() -> None:
    doc = _sparse_doc()
    del doc["nodes"]["n1"]["strategy"]
    with pytest.raises(SubgameError, match="n1 has no strategy"):
        subgame(doc, "n4", ("8h",), _hint())
    del doc["nodes"]["n0"]["reach_by_player"]
    with pytest.raises(SubgameError, match="no ancestor"):
        subgame(doc, "n4", ("8h",), _hint())


def test_chance_node_full_mode() -> None:
    doc = _flop_doc()
    doc["nodes"]["n1"]["actions"] = [{"token": "check", "child": "n6"}]
    doc["nodes"]["n6"] = {"type": "action", "street": "flop", "player": 2, "pot": 1000,
                          "stacks": [9700] * 3, "reach_by_player": doc["nodes"]["n0"]["reach_by_player"],
                          "strategy": [[1.0] for _ in COMBOS],
                          "actions": [{"token": "check", "child": "n7"}]}  # fmt: skip
    doc["nodes"]["n7"] = {"type": "chance", "street": "turn", "children": {"8h": "n8"}}
    spot = subgame(doc, "n7", ("8h",), _hint())
    assert spot["game"]["street"] == "turn" and spot["game"]["pot"] == 1000
    assert [p["position"] for p in spot["players"]] == ["BB", "CO", "BTN"]
    assert "last_aggressor" not in spot["game"]  # nobody bet the flop


def test_chance_node_with_own_fields_removes_dealt_card() -> None:
    doc = _flop_doc()
    reach = doc["nodes"]["n0"]["reach_by_player"]  # taken on arrival: still holds 8h8d
    doc["nodes"]["n1"]["actions"] = [{"token": "check", "child": "n7"}]
    doc["nodes"]["n7"] = {"type": "chance", "street": "turn", "pot": 1000, "stacks": [9700] * 3,
                          "reach_by_player": reach, "children": {"8h": "n8"}}  # fmt: skip
    spot = subgame(doc, "n7", ("8h",), _hint())
    assert spot["game"]["street"] == "turn" and spot["game"]["board"] == [*FLOP, "8h"]
    assert _range(spot, "BB") == {"AsAh": 1.0, "QcJc": 0.5}


def test_preflop_doc_to_flop() -> None:
    reach = [_dense({"AsKs": 1.0}), _dense({"QsQh": 1.0}), _dense({"7c7d": 1.0})]
    doc = {
        "meta": {}, "combos": COMBOS, "root": "n0",
        "players": [{"name": "btn", "position": "BTN", "hero": True},
                    {"name": "sb", "position": "SB", "hero": False},
                    {"name": "bb", "position": "BB", "hero": False}],
        "nodes": {
            "n0": {"type": "action", "street": "preflop", "player": 0, "pot": 150,
                   "stacks": [10000, 9950, 9900], "reach_by_player": reach,
                   "actions": [{"token": "raise300", "amount": 300, "child": "n1"}]},
            "n1": {"type": "action", "street": "preflop", "player": 1, "pot": 450,
                   "stacks": [9700, 9950, 9900], "reach_by_player": reach,
                   "actions": [{"token": "fold", "child": "n2"}]},
            "n2": {"type": "action", "street": "preflop", "player": 2, "pot": 450,
                   "stacks": [9700, 9950, 9900], "reach_by_player": reach,
                   "strategy": [[1.0] for _ in COMBOS], "actions": [{"token": "call", "child": "n3"}]},
            "n3": {"type": "terminal", "kind": "leaf", "pot": 650},
        },
    }  # fmt: skip
    seats = (Seat(None, "BTN", 10000, hero=True), Seat(None, "SB", 9950), Seat(None, "BB", 9900))
    hint = SpotRequest(seats=seats, street="preflop", board=(), pot=150, sb=50, bb=100, source="gto")
    spot = subgame(doc, "n3", ("Kd", "7s", "2c"), hint)
    g = spot["game"]
    assert g["street"] == "flop" and g["board"] == list(FLOP) and g["pot"] == 650
    assert [p["position"] for p in spot["players"]] == ["BB", "BTN"]
    assert [p["stack"] for p in spot["players"]] == [9700, 9700]  # BB's call: pot grew by 200
    assert g["last_aggressor"] == "BTN"
    assert _range(spot, "BB") == {"7d7c": 1.0}  # 7s on the board is a different card


def test_subgame_errors() -> None:
    doc = _flop_doc()
    with pytest.raises(SubgameError, match="does not end a street"):
        subgame(doc, "n1", ("8h",), _hint())
    with pytest.raises(SubgameError, match="board cards"):
        subgame(doc, "n4", ("8h", "9h"), _hint())
    with pytest.raises(SubgameError, match="no node"):
        subgame(doc, "n99", ("8h",), _hint())
    broke = copy.deepcopy(doc)
    broke["nodes"]["n4"]["stacks"] = [0, 9200, 9700]
    with pytest.raises(SubgameError, match="all-in"):
        subgame(broke, "n4", ("8h",), _hint())


# ------------------------------------------------------------------ action_ev_loss


def _ev_doc() -> dict:
    doc = _flop_doc()
    node = doc["nodes"]["n3"]
    node["reach"] = node["reach_by_player"][0]
    ev = [[0.0, 0.0] for _ in COMBOS]
    ev[IDX["AsAh"]] = [0.0, 850.5]
    ev[IDX["QcJc"]] = [0.0, -120.0]
    node["action_ev"] = ev
    return doc


def test_action_ev_loss() -> None:
    doc = _ev_doc()
    assert action_ev_loss(doc, "n3", "AhAs", "fold") == (pytest.approx(850.5), "call")
    assert action_ev_loss(doc, "n3", "AsAh", "call") == (0.0, "call")
    assert action_ev_loss(doc, "n3", "QcJc", "call") == (pytest.approx(120.0), "fold")
    assert ev_loss_for(doc, "n3", "QcJc", "fold") == (0.0, "fold")


def test_action_ev_loss_counterfactual_for_unreached_in_range_combo() -> None:
    doc = _ev_doc()
    node = doc["nodes"]["n3"]
    node["reach"] = [0.0] * len(COMBOS)  # BB's own strategy never gets here with any combo
    node["reach_by_player"] = [[0.0] * len(COMBOS), *node["reach_by_player"][1:]]
    assert action_ev_loss(doc, "n3", "AsAh", "fold") == (pytest.approx(850.5), "call")


def test_hu_zero_reach_rows_are_unavailable() -> None:
    doc = _ev_doc()
    node = doc["nodes"]["n3"]
    node["reach"] = [0.0] * len(COMBOS)
    assert action_ev_loss(doc, "n3", "AsAh", "fold")[1] == "call"  # mwsolver: counterfactual
    doc["meta"]["backend"] = "hu"
    with pytest.raises(EvUnavailable, match="hu has no counterfactual"):
        action_ev_loss(doc, "n3", "AsAh", "fold")
    del node["reach"]  # --compact: falls back to reach_by_player[actor]
    assert action_ev_loss(doc, "n3", "AsAh", "fold") == (pytest.approx(850.5), "call")


def test_action_ev_loss_errors() -> None:
    doc = _ev_doc()
    with pytest.raises(EvUnavailable, match="not in the acting player's range"):
        action_ev_loss(doc, "n3", "2h2d", "call")
    with pytest.raises(EvUnavailable, match="not an action"):
        action_ev_loss(doc, "n3", "AsAh", "raise100")
    with pytest.raises(EvUnavailable, match="not an action node"):
        action_ev_loss(doc, "n4", "AsAh", "call")
    old = _flop_doc()
    with pytest.raises(EvUnavailable, match="no action_ev"):
        action_ev_loss(old, "n3", "AsAh", "call")
