"""Adapter (stream F): spots built from the hand-written fixture hands (tests/fixtures/hands_*.json).

Covers preflop/flop/turn/river, heads-up and multiway, 8-max and straddle tables: players,
stacks, pot, board and action order per the hand; GTO chart ranges for every seat and no
villain locks (Plan 7); TOML round-trip; pn.adapter.validate.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from pn import ranges
from pn.adapter.spot import SpotError, build_spot, build_spot_from_hand, preflop_sizes
from pn.adapter.validate import SpotValidationError, to_toml, validate_config

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN_TOML = FIXTURES / "adapter_hu_flop.toml"
STACK = 5000


@pytest.fixture(scope="module")
def hands() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name in ("hands_core.json", "hands_special.json"):
        for h in json.loads((FIXTURES / name).read_text()):
            out[h["hand_id"]] = h
    return out


def _positions(spot) -> list[str]:
    return [p["position"] for p in spot.config["players"]]


def _range(spot, position: str) -> dict[str, float]:
    p = next(p for p in spot.config["players"] if p["position"] == position)
    return ranges.parse(p["range"])


# ------------------------------------------------------------------ state per street


def test_flop_heads_up_srp(hands: dict) -> None:
    spot = build_spot_from_hand(hands["c02_cbet33_fold"], "flop", "pC")
    g = spot.config["game"]
    assert g["street"] == "flop"
    assert g["board"] == ["Kd", "7s", "2c"]
    assert g["pot"] == 125 + 125 + 25  # CO open + BB call + SB dead blind
    assert _positions(spot) == ["BB", "CO"]  # postflop action order
    assert [p["stack"] for p in spot.config["players"]] == [STACK - 125] * 2
    assert g["last_aggressor"] == "CO"
    assert spot.players[spot.hero_index()].player_id == "pC"
    assert spot.config["players"][1]["hero"] is True
    assert "lock" not in spot.config["players"][1]  # hero is never locked


def test_flop_multiway_and_turn_after_fold(hands: dict) -> None:
    hand = hands["c10_multiway_cbet_barrel"]
    flop = build_spot_from_hand(hand, "flop", "pA")
    assert _positions(flop) == ["LJ", "CO", "BTN"]
    assert flop.config["game"]["pot"] == 3 * 125 + 25 + 50
    turn = build_spot_from_hand(hand, "turn", "pA")
    assert _positions(turn) == ["LJ", "BTN"]  # CO folded on the flop
    assert turn.config["game"]["board"] == ["Qh", "Jd", "3s", "8c"]
    assert turn.config["game"]["pot"] == 450 + 2 * 150
    assert [p["stack"] for p in turn.config["players"]] == [STACK - 125 - 150] * 2


def test_river_known_hero_cards_are_dead_for_villain(hands: dict) -> None:
    spot = build_spot_from_hand(hands["c12_triple_barrel_sd"], "river", "pB")
    g = spot.config["game"]
    assert g["board"] == ["Ks", "8h", "4c", "2d", "9s"]
    assert g["pot"] == 275 + 2 * 180 + 2 * 475
    assert [p["stack"] for p in spot.config["players"]] == [STACK - 125 - 180 - 475] * 2
    assert spot.hero_cards == ("Ah", "Kd")
    villain = _range(spot, "BB")
    assert villain and not any("Ah" in c or "Kd" in c for c in villain)
    board = set(g["board"])
    for pos in ("BB", "HJ"):
        assert not any(c[:2] in board or c[2:] in board for c in _range(spot, pos))


def test_preflop_spot_keeps_blinds_and_drops_early_folders(hands: dict) -> None:
    hand = hands["c04_4bet_5bet"]
    hj = build_spot_from_hand(hand, "preflop", "pB")  # HJ's first decision: LJ opened, nobody folded
    g = hj.config["game"]
    assert _positions(hj) == ["LJ", "HJ", "CO", "BTN", "SB", "BB"]
    assert g["pot"] == 75 and g["sb"] == 25 and g["bb"] == 50 and g["board"] == []
    stacks = {p["position"]: p["stack"] for p in hj.config["players"]}
    assert stacks["SB"] == STACK - 25 and stacks["BB"] == STACK - 50 and stacks["LJ"] == STACK
    pf = hj.config["sizes"]["preflop"]
    assert pf["open"] == [250]  # 125 / 50
    assert pf["three_bet_ip"] == [300]  # HJ 3-bets in position: 375 / 125
    assert pf["four_bet"] == [220]  # 825 / 375
    bb = build_spot_from_hand(hand, "preflop", "pF")  # BB acts after CO, BTN, SB folded
    assert _positions(bb) == ["LJ", "HJ", "BB"]
    assert bb.config["game"]["pot"] == 75  # SB's blind stays in as dead money


def test_squeeze_size_is_derived(hands: dict) -> None:
    pf = preflop_sizes(hands["c09_squeeze"])
    assert pf["open"] == [250]
    assert pf["squeeze"] == {"base": 340, "per_caller": 100}  # 550/125 = 440% with one caller


def test_heads_up_positions(hands: dict) -> None:
    hand = hands["s03_heads_up"]
    pre = build_spot_from_hand(hand, "preflop", "pA")
    assert _positions(pre) == ["SB", "BB"]  # HU button posts the SB and acts first preflop
    river = build_spot_from_hand(hand, "river", "pA")
    assert _positions(river) == ["BB", "BTN"]
    assert river.config["game"]["pot"] == 2 * 125 + 2 * 80


def test_eight_max_multiway_flop(hands: dict) -> None:
    spot = build_spot_from_hand(hands["s04_eight_max_multiway"], "flop", "pA")
    assert _positions(spot) == ["BB", "UTG", "UTG1"]
    assert spot.config["game"]["pot"] == 3 * 125 + 25


def test_straddle_hand_flop_without_straddler(hands: dict) -> None:
    spot = build_spot_from_hand(hands["s02_straddle"], "flop", "pB")
    g = spot.config["game"]
    assert _positions(spot) == ["HJ", "CO"]
    assert g["pot"] == 2 * 300 + 25 + 50 + 100  # folded blinds and straddle are dead money
    assert "straddle" not in g  # STR is not a listed player


def test_errors(hands: dict) -> None:
    with pytest.raises(SpotError, match="did not reach"):
        build_spot_from_hand(hands["c02_cbet33_fold"], "turn", "pC")
    with pytest.raises(SpotError, match="not in the pot"):
        build_spot_from_hand(hands["c02_cbet33_fold"], "flop", "pA")
    with pytest.raises(SpotError, match="all-in"):
        build_spot_from_hand(hands["c20_allin_pre_sd"], "flop", "pF")
    with pytest.raises(SpotError, match="hero"):
        build_spot_from_hand(hands["c02_cbet33_fold"], "flop", "nobody")


# ------------------------------------------------------------------ ranges


def test_ranges_from_charts_without_profiles(hands: dict) -> None:
    spot = build_spot_from_hand(hands["c02_cbet33_fold"], "flop", "pC")
    by_pos = {p.position: p for p in spot.players}
    assert by_pos["CO"].range_source == "pc:open CO"  # hero: chart range of its line
    assert by_pos["BB"].range_source == "pc:call BB vs CO"  # villain: chart, never a profile
    assert by_pos["BB"].lock_source is None and not spot.warnings


def test_profiles_supply_hand_counts_and_tips_only(hands: dict) -> None:
    hand = hands["c02_cbet33_fold"]
    profiles = {"pF": {"hands": 35, "tips": ["Folds too much to cbets."]}}
    spot = build_spot_from_hand(hand, "flop", "pC", profiles=profiles)
    bb = spot.players[0]
    assert bb.profile_hands == 35 and bb.tip == "Folds too much to cbets."
    assert _range(spot, "BB") == _range(build_spot_from_hand(hand, "flop", "pC"), "BB")


def test_gto_spot_has_no_locks(hands: dict) -> None:
    spot = build_spot_from_hand(hands["c02_cbet33_fold"], "flop", "pC")
    assert all("lock" not in p for p in spot.config["players"])
    assert "GTO" in spot.toml.splitlines()[0]


# ------------------------------------------------------------------ TOML, validation, solver


ALL_SPOTS = [
    ("c02_cbet33_fold", "flop", "pC"),
    ("c10_multiway_cbet_barrel", "flop", "pA"),
    ("c10_multiway_cbet_barrel", "turn", "pD"),
    ("c12_triple_barrel_sd", "river", "pF"),
    ("c04_4bet_5bet", "preflop", "pB"),
    ("c09_squeeze", "preflop", "pE"),
    ("s03_heads_up", "preflop", "pB"),
    ("s03_heads_up", "turn", "pB"),
    ("s04_eight_max_multiway", "turn", "pB"),
    ("s02_straddle", "flop", "pC"),
    ("s08_deep_hu_3bet", "preflop", "pA"),
]


@pytest.mark.parametrize(("hand_id", "street", "hero"), ALL_SPOTS)
def test_toml_round_trips_and_validates(hands: dict, hand_id: str, street: str, hero: str) -> None:
    spot = build_spot_from_hand(hands[hand_id], street, hero)
    parsed = tomllib.loads(spot.toml)
    assert parsed == json.loads(json.dumps(spot.config))  # tuples -> lists
    validate_config(parsed)


def test_golden_toml_validates_and_round_trips() -> None:
    cfg = tomllib.loads(GOLDEN_TOML.read_text())
    validate_config(cfg)
    assert tomllib.loads(to_toml(cfg)) == cfg


def test_validate_rejects_contract_violations() -> None:
    cfg = tomllib.loads(GOLDEN_TOML.read_text())
    bad_order = {**cfg, "players": list(reversed(cfg["players"]))}
    with pytest.raises(SpotValidationError, match="action order"):
        validate_config(bad_order)
    with pytest.raises(SpotValidationError, match="turn_bets"):
        validate_config({**cfg, "sizes": {**cfg["sizes"], "turn_bets": [50]}})
    with pytest.raises(SpotValidationError, match="board"):
        validate_config({**cfg, "game": {**cfg["game"], "board": ["Kd", "Ah"]}})


def test_validate_rejects_node_locks_but_keeps_the_fold_lock(hands: dict) -> None:
    cfg = tomllib.loads(GOLDEN_TOML.read_text())
    locked = {**cfg, "players": [cfg["players"][0],
                                 {**cfg["players"][1], "lock": {"flop:*:first": {"check": 1.0}}}]}  # fmt: skip
    with pytest.raises(SpotValidationError, match=r"unknown keys \['lock'\]"):
        validate_config(locked)
    folded = build_spot_from_hand(hands["c04_4bet_5bet"], "preflop", "pA", upto_seq=8)
    validate_config(tomllib.loads(folded.toml))  # a folded BB keeps its fold lock
    bb = folded.config["players"][2]
    with pytest.raises(SpotValidationError, match="unknown keys"):
        validate_config({**folded.config, "players": [*folded.config["players"][:2],
                                                      {**bb, "lock": {"preflop:*:*": {"call": 1.0}}}]})  # fmt: skip


# ------------------------------------------------------------------ from the ingest DB


def test_build_spot_from_db(hands: dict, tmp_path: Path) -> None:
    from pn.ingest import db as ingest_db

    path = tmp_path / "pn.sqlite"
    ingest_db.load(list(hands.values()), path)
    spot = build_spot(path, "c02_cbet33_fold", "flop", "pC")
    assert _positions(spot) == ["BB", "CO"]
    assert spot.players[0].lock_source is None
    with pytest.raises(SpotError, match="not found"):
        build_spot(path, "nope", "flop", "pC")


def test_spot_cli_writes_toml(hands: dict, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from pn.cli.main import app
    from pn.ingest import db as ingest_db

    path = tmp_path / "pn.sqlite"
    ingest_db.load(list(hands.values()), path)
    out = tmp_path / "spot.toml"
    r = CliRunner().invoke(app, ["spot", "c12_triple_barrel_sd", "--street", "turn", "--hero", "pF",
                                 "--db", str(path), "--out", str(out)])  # fmt: skip
    assert r.exit_code == 0, r.output
    cfg = tomllib.loads(out.read_text())
    assert cfg["game"]["street"] == "turn" and len(cfg["game"]["board"]) == 4
    bad = CliRunner().invoke(app, ["spot", "c02_cbet33_fold", "--street", "river", "--db", str(path)])
    assert bad.exit_code == 1
