"""pn.solve seat ranges and build_spot (contracts/solve_service.md, Plan 7: GTO ranges only),
on the fixture hands used by test_adapter_spot.py and on anonymous `pn solve new` requests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pn import ranges
from pn.adapter.spot import build_spot_from_hand
from pn.solve import Seat, SpotError, SpotRequest, build, build_spot
from pn.solve.request import new_request, parse_board

FIXTURES = Path(__file__).parent / "fixtures"
HAND, HERO, VILLAIN = "c02_cbet33_fold", "pC", "pF"  # CO opens, BB (pF) calls; flop Kd7s2c


@pytest.fixture(scope="module")
def hands() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name in ("hands_core.json", "hands_special.json"):
        for h in json.loads((FIXTURES / name).read_text()):
            out[h["hand_id"]] = h
    return out


def _villain(spot):
    return next(p for p in spot.players if p.player_id == VILLAIN)


def _hero(spot):
    return next(p for p in spot.players if p.hero)


def test_gto_ranges_and_no_locks(hands: dict) -> None:
    profiles = {VILLAIN: {"hands": 100, "tips": ["x"]}}
    spot = build_spot_from_hand(hands[HAND], "flop", HERO, profiles=profiles)
    v, h = _villain(spot), _hero(spot)
    assert v.range_source == "pc:call BB vs CO" and v.lock_source is None
    assert h.range_source == "pc:open CO" and h.lock_source is None
    assert all("lock" not in p for p in spot.config["players"])
    plain = build_spot_from_hand(hands[HAND], "flop", HERO)
    assert spot.config == plain.config  # profiles add hand counts and tips, not ranges


def test_default_source_is_gto(hands: dict) -> None:
    a = build_spot_from_hand(hands[HAND], "flop", HERO)
    b = build_spot_from_hand(hands[HAND], "flop", HERO, source="gto")
    assert a.config == b.config


def test_override_replaces_range(hands: dict) -> None:
    spot = build_spot_from_hand(hands[HAND], "flop", HERO, overrides={VILLAIN: "KK,AKs,77"})
    v = _villain(spot)
    assert v.range_source == "override" and v.lock_source is None
    rng = ranges.parse(spot.config["players"][0]["range"])
    board = {"Kd", "7s", "2c"}
    want = {c for c in ranges.parse("KK,AKs,77") if not {c[:2], c[2:]} & board}
    assert set(rng) == want  # override text minus board cards


def test_preflop_root_is_any_two(hands: dict) -> None:
    spot = build_spot_from_hand(hands["c04_4bet_5bet"], "preflop", "pB")
    assert {p.range_source for p in spot.players} == {"any_two"}


def test_preflop_seat_with_line_prefers_pokercoaching_then_gto_wizard() -> None:
    seats = (Seat(None, "BTN", 9700, line="open"), Seat(None, "BB", 9900, hero=True))
    built = build(SpotRequest(seats=seats, street="preflop", board=(), pot=150, sb=50, bb=100))
    btn = next(r for r in built.seats if r.seat.position == "BTN")
    assert btn.range_origin == "pc:open BTN"
    assert next(r for r in built.seats if r.seat.position == "BB").range_origin == "any_two"
    seats = (Seat(None, "BTN", 9700, line="open"), Seat(None, "SB", 9700, line="call", vs="BTN"),
             Seat(None, "BB", 9900, hero=True))  # fmt: skip
    built = build(SpotRequest(seats=seats, street="preflop", board=(), pot=650, sb=50, bb=100))
    sb = next(r for r in built.seats if r.seat.position == "SB")
    assert sb.range_origin == "gtow:100|vs_open|BTN|SB"  # PokerCoaching has no SB flat chart
    rng = ranges.parse(sb.range_text)
    assert rng and "7c2d" not in rng


def test_pre_plan7_saved_request_loads_as_gto() -> None:
    from pn.solve.request import request_from_json, request_to_json

    req = new_request(["BTN", "BB"], 100, board=parse_board("Kd7s2c"), line="srp", hero="BTN")
    saved = {**request_to_json(req), "source": "tendencies",
             "lock_overrides": {"BB": {"flop:*:first": {"check": 1.0}}}}  # fmt: skip
    loaded = request_from_json(saved)
    assert loaded.source == "gto" and not hasattr(loaded, "lock_overrides")
    assert loaded == req
    assert all("lock" not in p for p in build(loaded).config["players"])


def test_new_request_srp_and_3bp_pot_and_stacks() -> None:
    srp = build_spot(new_request(["BB", "BTN"], 100, board=parse_board("Kd2cKh"), line="srp"))
    g = srp["game"]
    assert g["pot"] == 300 + 300 + 50 and g["last_aggressor"] == "BTN"
    assert [p["position"] for p in srp["players"]] == ["BB", "BTN"]
    assert {p["stack"] for p in srp["players"]} == {9700}
    tbp = build(new_request(["BTN", "BB"], 200, board=parse_board("Kd2cKh"), line="3bp"))
    assert tbp.config["game"]["pot"] == 2 * 1050 + 50  # BB 3-bets 3bb to 3.5x OOP
    assert tbp.config["game"]["last_aggressor"] == "BB"
    origins = {r.seat.position: r.range_origin for r in tbp.seats}
    assert origins["BB"] == "pc:3bet BB vs BTN" and origins["BTN"].startswith("chart:call_3bet")


def test_new_request_preflop_root_and_errors() -> None:
    req = new_request(["LJ", "HJ", "CO", "BTN", "SB", "BB"], 100)
    spot = build_spot(req)
    assert spot["game"]["pot"] == 150 and spot["game"]["street"] == "preflop"
    assert [p["stack"] for p in spot["players"]] == [10000] * 4 + [9950, 9900]
    with pytest.raises(ValueError, match="--line"):
        new_request(["BTN", "BB"], 100, board=parse_board("Kd2cKh"))
    with pytest.raises(ValueError, match="positions"):
        new_request(["UTG", "BB"], 100)  # not a 6-max seat
    explicit = build(new_request(["BTN", "BB"], 100, seat_ranges={"BB": "AA"}))
    assert next(r for r in explicit.seats if r.seat.position == "BB").range_text == "AA"


def test_explicit_seat_range_fully_dead_raises() -> None:
    req = new_request(["BTN", "BB"], 100, board=parse_board("AsAhKd"), line="srp",
                      seat_ranges={"BB": "AsAh"})  # fmt: skip
    with pytest.raises(SpotError, match="empty range"):
        build_spot(req)


# ------------------------------------------------------------------ preflop root knobs


def _root(table: int, knobs: dict | None = None) -> SpotRequest:
    from dataclasses import replace

    from pn.solve.request import ROOT_KNOBS, TABLE_SEATS

    req = new_request(list(TABLE_SEATS[table]), 100, table=table)
    return replace(req, sizes={"preflop": {**ROOT_KNOBS[table], **(knobs or {})}})


def test_root_knob_defaults_and_overrides() -> None:
    from pn.solve import spot_key

    six = build_spot(_root(6))["sizes"]["preflop"]
    assert six == {"limp": "sb_only", "cold_call_3bet": False}
    eight = build_spot(_root(8))["sizes"]["preflop"]
    assert eight == {"limp": "sb_only", "cold_call_3bet": False, "max_callers": 2}
    over = build_spot(_root(6, {"limp": "all", "max_callers": 3}))
    assert over["sizes"]["preflop"] == {"limp": "all", "cold_call_3bet": False, "max_callers": 3}
    assert spot_key(over) != spot_key(build_spot(_root(6)))
    flop = build_spot(new_request(["BTN", "BB"], 100, board=parse_board("Kd2cKh"), line="srp"))
    assert "preflop" not in flop["sizes"]


@pytest.mark.parametrize("knobs", [{"limp": "sometimes"}, {"cold_call_3bet": 1},
                                   {"max_callers": 0}])  # fmt: skip
def test_validator_rejects_bad_knobs(knobs: dict) -> None:
    from pn.adapter.validate import SpotValidationError

    with pytest.raises(SpotValidationError, match="sizes.preflop"):
        build_spot(_root(6, knobs))


def test_hand_preflop_multiway_gets_knobs_and_heads_up_does_not(hands: dict) -> None:
    six = build_spot_from_hand(hands["c04_4bet_5bet"], "preflop", "pB")  # 6 players listed
    pf = six.config["sizes"]["preflop"]
    assert pf["limp"] == "sb_only" and pf["cold_call_3bet"] is False and "max_callers" not in pf
    assert pf["open"] == [250]  # the hand's sizes are kept alongside the knobs
    hu = build_spot_from_hand(hands["s03_heads_up"], "preflop", "pA")
    assert not {"limp", "cold_call_3bet", "max_callers"} & set(hu.config["sizes"]["preflop"])


def test_hand_with_limps_relaxes_limp_knob(hands: dict) -> None:
    spot = build_spot_from_hand(hands["c06_limped_checkdown"], "preflop", "pA")
    assert spot.config["sizes"]["preflop"]["limp"] == "all"
    assert "relaxed for actions taken in the hand: limp=all" in spot.toml.splitlines()[1]
    sb_complete = build_spot_from_hand(hands["c08_sb_limp_bet"], "preflop", "pE")
    assert sb_complete.config["sizes"]["preflop"].get("limp", "sb_only") == "sb_only"


def test_knob_relaxations_cold_call_and_callers() -> None:
    from pn.adapter.spot import knob_relaxations

    pos = {"p1": "UTG", "p2": "LJ", "p3": "HJ", "p4": "CO", "p5": "BTN", "p6": "SB", "p7": "BB"}

    def hand(acts: list[tuple[str, str]]) -> dict:
        return {"players": [{"player_id": k, "position": v} for k, v in pos.items()],
                "actions": [{"street": "preflop", "player_id": p, "kind": k} for p, k in acts]}  # fmt: skip

    cold = hand([("p1", "raise"), ("p2", "raise"), ("p3", "call")])
    assert knob_relaxations(cold) == {"cold_call_3bet": True}
    flat_then_call = hand([("p1", "raise"), ("p2", "call"), ("p3", "raise"), ("p2", "call")])
    assert knob_relaxations(flat_then_call) == {}  # p2 already had chips in
    many = hand([("p1", "raise"), ("p2", "call"), ("p3", "call"), ("p4", "call")])
    assert knob_relaxations(many) == {"max_callers": 8}
    assert knob_relaxations(hand([("p6", "call"), ("p7", "check")])) == {}  # SB completes


def test_folded_bb_stays_listed_and_locked_to_fold(hands: dict) -> None:
    spot = build_spot_from_hand(hands["c04_4bet_5bet"], "preflop", "pA", upto_seq=8)
    assert [p["position"] for p in spot.config["players"]] == ["LJ", "HJ", "BB"]
    bb = spot.config["players"][2]
    assert bb["lock"] == {"preflop:*:*": {"fold": 1.0, "order": "uniform"}}
    assert spot.players[2].lock_source == "folded" and bb["stack"] == 4950
    assert spot.config["game"]["pot"] == 75  # SB's and BB's blinds stay in the pot
