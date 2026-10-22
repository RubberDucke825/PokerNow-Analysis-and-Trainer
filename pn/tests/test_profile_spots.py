"""spots.py: positional keys, preflop situations, opener groups and IP/OOP tags (hand-checked)."""

from __future__ import annotations

from test_profile_fixture_build import SIX, build

from pn.profile.spots import opener_group, pos_key, stat_street
from pn.profile.walk import walk_hand

BOARD = "Kd 7s 2c 9h 4d"


def _facts(script: str, seats=SIX, board: str = ""):
    return walk_hand(build("h", seats, script, board)).players


def test_pos_key_and_groups() -> None:
    assert pos_key("BTN", 2) == "BTN_HU" and pos_key("BB", 2) == "BB_HU"
    assert pos_key("BTN", 6) == "BTN" and pos_key("CO", 2) == "CO"
    assert [opener_group(k) for k in ("UTG", "LJ", "HJ", "CO", "BTN", "SB", "BTN_HU")] == [
        "EP", "EP", "MP", "CO", "BTN", "SB", "BTN_HU",
    ]  # fmt: skip
    assert stat_street("raise_cbet_turn") == "turn" and stat_street("float_turn") == "turn"
    assert stat_street("three_bet") is None


def test_rfi_vs_open_cold_vs_3bet_vs_4bet() -> None:
    # CO opens, BTN 3-bets, blinds fold (cold), CO 4-bets, BTN folds
    pf = _facts("pA f; pB f; pC r 125; pD r 375; pE f; pF f; pC r 900; pD f")
    assert pf["pA"].situ["rfi"] == ("rfi", "LJ") and pf["pB"].situ["rfi"] == ("rfi", "HJ")
    co, btn, sb = pf["pC"], pf["pD"], pf["pE"]
    assert co.situ["rfi"] == co.situ["steal"] == ("rfi", "CO")
    assert co.situ["fold_to_three_bet"] == co.situ["four_bet"] == ("vs_3bet", "CO", "BTN")
    assert btn.situ["three_bet"] == ("vs_open", "CO", "BTN")
    assert btn.group == {"three_bet": "CO", "cold_call": "CO"}
    assert btn.situ["fold_to_four_bet"] == ("vs_4bet", "CO", "BTN")
    assert btn.stats["fold_to_four_bet"] == (True, True)
    assert sb.situ["four_bet"] == ("cold", "CO", "BTN", "SB")
    assert pf["pF"].situ["vpip"] == ("cold", "CO", "BTN", "BB")
    assert all(p.pos_key == p.position for p in pf.values())


def test_heads_up_seats_steal_and_ip_oop() -> None:
    script = "pV r 125; pH c; F; pH x; pV b 85; pH c; T; pH x; pV x; R; pH b 200; pV f"
    pf = _facts(script, [("pV", "BTN"), ("pH", "BB")], BOARD)
    btn, bb = pf["pV"], pf["pH"]
    assert (btn.pos_key, bb.pos_key) == ("BTN_HU", "BB_HU")
    assert btn.situ["rfi"] == ("rfi", "BTN_HU")
    assert bb.situ["three_bet"] == bb.situ["fold_to_steal"] == ("vs_open", "BTN_HU", "BB_HU")
    assert bb.group["fold_to_steal"] == "BTN_HU" and bb.stats["fold_to_steal"] == (True, False)
    assert btn.street_ip == {"flop": True, "turn": True, "river": True}
    assert bb.street_ip == {"flop": False, "turn": False, "river": False}
    assert btn.ipos["cbet_flop"] == "IP"
    assert bb.ipos["fold_to_cbet_flop"] == bb.ipos["raise_cbet_flop"] == "OOP"
    assert bb.stats["raise_cbet_flop"] == (True, False)


def test_squeeze_and_multiway_have_no_gto_situation() -> None:
    # LJ opens, HJ calls, BB squeezes: nobody's decision after the call maps to a GTO spot
    pf = _facts("pA r 125; pB c; pC f; pD f; pE f; pF r 500; pA f; pB f")
    assert pf["pB"].situ["three_bet"] == ("vs_open", "LJ", "HJ")  # before the call: clean
    for p in ("pC", "pD", "pE", "pF"):
        assert pf[p].situ["three_bet"] is None and "three_bet" not in pf[p].group
    assert pf["pA"].situ["fold_to_three_bet"] is None  # squeezed opener, not a vs_3bet spot
    assert pf["pA"].stats["fold_to_three_bet"] == (True, True)  # still counted as a stat


def test_fold_to_steal_needs_a_lone_open() -> None:
    clean = _facts("pA f; pB f; pC f; pD r 125; pE f; pF c; F; pF x; pD x", board=BOARD)
    assert clean["pE"].stats["fold_to_steal"] == (True, True)
    assert clean["pF"].stats["fold_to_steal"] == (True, False)
    assert clean["pE"].group["fold_to_steal"] == "BTN"
    assert clean["pF"].ipos.get("donk_flop") == "OOP"
    # CO limps, BTN isolates: an iso-raise is not a steal
    iso = _facts("pA f; pB f; pC c; pD r 200; pE f; pF f; pC f")
    assert "fold_to_steal" not in iso["pE"].stats and "fold_to_steal" not in iso["pF"].stats
    assert iso["pE"].situ["three_bet"] is None


def test_raise_war_four_five_six_bet() -> None:
    # CO opens, BTN 3-bets, CO 4-bets, BTN 5-bets, CO 6-bets all-in, BTN folds
    pf = _facts("pA f; pB f; pC r 125; pD r 375; pE f; pF f; pC r 900; pD r 2000; pC r 5000; pD f")
    co, btn = pf["pC"], pf["pD"]
    assert btn.stats["fold_to_four_bet"] == (True, False) and btn.stats["five_bet"] == (True, True)
    assert btn.situ["five_bet"] == btn.situ["fold_to_four_bet"] == ("vs_4bet", "CO", "BTN")
    assert co.stats["fold_to_five_bet"] == (True, False) and co.stats["six_bet"] == (True, True)
    assert co.situ["six_bet"] == ("vs_5bet", "CO", "BTN")
    assert btn.stats["fold_to_six_bet"] == (True, True)
    assert btn.situ["fold_to_six_bet"] == ("vs_6bet", "CO", "BTN")


def test_cold_four_bet_is_not_a_vs_4bet_spot() -> None:
    # CO opens, BTN 3-bets, SB cold 4-bets: BTN faces a 4-bet, but not the opener's
    pf = _facts("pA f; pB f; pC r 125; pD r 375; pE r 1000; pF f; pC f; pD f")
    assert pf["pD"].stats["fold_to_four_bet"] == (True, True)
    assert pf["pD"].situ["fold_to_four_bet"] is None and pf["pD"].situ["five_bet"] is None
