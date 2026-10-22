"""Exact stat counts on the hand-written fixtures (expected values computed by hand)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pn.profile.stats import RawStats, aggregate, walk_all
from pn.profile.walk import size_bucket, walk_hand

FIX = Path(__file__).parent / "fixtures"


def _hands(name: str) -> list[dict]:
    return json.loads((FIX / name).read_text())


@pytest.fixture(scope="module")
def core() -> dict[str, RawStats]:
    return aggregate(walk_all(_hands("hands_core.json")))


@pytest.fixture(scope="module")
def special() -> dict[str, RawStats]:
    return aggregate(walk_all(_hands("hands_special.json")))


# (opportunities, successes) for alice (pA, always LJ, first to act preflop)
ALICE = {
    "vpip": (24, 11), "pfr": (24, 9), "rfi": (24, 8), "limp": (24, 3), "three_bet": (2, 1),
    "four_bet": (5, 1), "fold_to_three_bet": (5, 2), "cbet_flop": (1, 1), "cbet_turn": (1, 1),
    "fold_to_cbet_flop": (2, 1), "fold_to_cbet_turn": (1, 1), "check_raise_flop": (2, 0),
    "donk_flop": (2, 0), "wtsd": (6, 2), "wsd": (2, 1), "wwsf": (6, 2), "af_flop": (2, 1),
    "af_turn": (1, 1),
}  # fmt: skip
ALICE_ABSENT = ("cold_call", "steal", "squeeze", "probe_turn", "cbet_river", "af_river")

# frank (pF, always BB)
FRANK = {
    "vpip": (23, 12), "pfr": (23, 1), "three_bet": (16, 1), "cold_call": (16, 11),
    "squeeze": (2, 0), "four_bet": (5, 0), "donk_flop": (11, 2), "fold_to_cbet_flop": (7, 2),
    "fold_to_cbet_turn": (3, 1), "fold_to_cbet_river": (1, 0), "check_raise_flop": (7, 1),
    "probe_turn": (2, 1), "wtsd": (14, 5), "wsd": (5, 1), "wwsf": (14, 4), "af_flop": (7, 3),
    "af_turn": (3, 1), "af_river": (5, 3),
}  # fmt: skip
FRANK_ABSENT = ("limp", "rfi", "steal", "fold_to_three_bet", "cbet_flop")


@pytest.mark.parametrize("stat,expected", ALICE.items())
def test_alice_counts(core: dict[str, RawStats], stat: str, expected: tuple[int, int]) -> None:
    assert core["pA"].count(stat) == expected


@pytest.mark.parametrize("stat,expected", FRANK.items())
def test_frank_counts(core: dict[str, RawStats], stat: str, expected: tuple[int, int]) -> None:
    assert core["pF"].count(stat) == expected


def test_absent_stats_have_no_opportunities(core: dict[str, RawStats]) -> None:
    assert all(core["pA"].count(s) == (0, 0) for s in ALICE_ABSENT)
    assert all(core["pF"].count(s) == (0, 0) for s in FRANK_ABSENT)


def test_hand_and_showdown_counts(core: dict[str, RawStats]) -> None:
    assert core["pA"].hands == 24 and core["pF"].hands == 24
    assert core["pA"].showdowns_seen == 2
    assert core["pF"].showdowns_seen == 5


def test_steal_and_cbet(core: dict[str, RawStats]) -> None:
    assert core["pD"].count("steal") == (6, 4)
    assert core["pC"].count("cbet_flop") == (6, 4)
    assert core["pC"].count("cbet_turn") == (2, 1)


def test_fold_vs_size_buckets(core: dict[str, RawStats]) -> None:
    assert core["pF"].fold_vs_size == {
        "flop": {"33": [3, 1], "50": [3, 1], "100": [1, 1], "25": [1, 0]},
        "turn": {"75": [1, 0], "150": [2, 1]},
        "river": {"75": [2, 1]},
    }
    assert core["pA"].fold_vs_size == {
        "flop": {"50": [1, 0], "100": [1, 1], "33": [1, 1]},
        "turn": {"75": [1, 1]},
    }


def test_bet_size_hist(core: dict[str, RawStats]) -> None:
    assert core["pF"].bet_size_hist == {
        "flop": {"45-70": 1, "70-120": 1},
        "turn": {"45-70": 1},
        "river": {"45-70": 1, "70-120": 2},
    }
    assert core["pA"].bet_size_hist == {"flop": {"30-45": 1}, "turn": {"70-120": 1}}


def test_players_in_pot_split(core: dict[str, RawStats]) -> None:
    assert core["pF"].by_pip["multiway"]["wtsd"] == [2, 1]
    assert core["pF"].by_pip["hu"]["wtsd"] == [12, 4]
    assert core["pA"].by_pip["multiway"]["cbet_flop"] == [1, 1]
    assert core["pA"].by_pip["hu"]["cbet_turn"] == [1, 1]


def test_by_position(core: dict[str, RawStats]) -> None:
    assert core["pA"].by_position["LJ"]["vpip"] == [24, 11]
    assert core["pF"].by_position["BB"]["cold_call"] == [16, 11]


@pytest.mark.parametrize(
    "street,pct,bucket",
    [("flop", 25.5, "25"), ("flop", 28.6, "25"), ("flop", 30.9, "33"), ("flop", 47.4, "50"),
     ("flop", 70.8, "100"), ("flop", 75, "100"), ("turn", 54.5, "75"), ("turn", 106.2, "150"),
     ("river", 149.6, "150"), ("river", 300, "150")],
)  # fmt: skip
def test_size_bucket_boundaries(street: str, pct: float, bucket: str) -> None:
    assert size_bucket(street, pct) == bucket


def test_skipped_hands(special: dict[str, RawStats]) -> None:
    hands = {h["hand_id"]: h for h in _hands("hands_special.json")}
    assert walk_hand(hands["s05_bomb_pot_skipped"]) is None
    assert walk_hand(hands["s06_incomplete_skipped"]) is None
    # pC counted in s01 (fold), s02 (open), s04 (fold), s07 (fold), s09 (fold)
    assert special["pC"].hands == 5


def test_straddle_limp_and_iso(special: dict[str, RawStats]) -> None:
    hf = walk_hand(next(h for h in _hands("hands_special.json") if h["hand_id"] == "s02_straddle"))
    assert hf is not None
    bob = hf.players["pB"].stats
    carol = hf.players["pC"].stats
    assert bob["limp"] == (True, True)  # calling the straddle unopened is a limp
    assert "rfi" not in carol  # a limper was in: not raise-first-in
    assert carol["cbet_flop"] == (True, True)
    assert hf.players["pA"].stats["vpip"] == (True, False)  # straddle post is not voluntary


def test_heads_up_and_run_it_twice(special: dict[str, RawStats]) -> None:
    hands = {h["hand_id"]: h for h in _hands("hands_special.json")}
    hu = walk_hand(hands["s03_heads_up"])
    assert hu is not None
    a = hu.players["pA"]
    assert a.stats["steal"] == (True, True)  # HU BTN open is a steal
    assert a.stats["cbet_flop"] == (True, True) and a.stats["cbet_turn"] == (True, False)
    rit = walk_hand(hands["s01_run_it_twice"])
    assert rit is not None
    assert rit.players["pA"].went_to_sd and rit.players["pA"].won and rit.players["pF"].won


def test_eight_max_multiway_cbet_fold(special: dict[str, RawStats]) -> None:
    hf = walk_hand(next(h for h in _hands("hands_special.json") if h["hand_id"].startswith("s04")))
    assert hf is not None
    assert hf.players["pA"].stats["cbet_flop"] == (True, True)
    assert hf.players["pA"].pip["cbet_flop"] == "multiway"
    assert hf.players["pB"].stats["fold_to_cbet_flop"] == (True, False)
    assert hf.players["pF"].stats["fold_to_cbet_flop"] == (True, True)
    assert "probe_turn" not in hf.players["pB"].stats  # flop was bet: no probe
    assert hf.players["pA"].stats["cbet_turn"] == (True, False)


def test_alias_merge_combines_counts() -> None:
    hands = _hands("hands_core.json")
    raw = aggregate(walk_all(hands), {"pA": "human1", "pF": "human1"})
    assert raw["human1"].count("vpip") == (24 + 23, 11 + 12)
    assert raw["human1"].hands == 48


def test_squeeze_responses(core: dict[str, RawStats], special: dict[str, RawStats]) -> None:
    # c09: A opens, B calls, C squeezes, A and B fold. c21: F could squeeze (did not).
    assert core["pA"].count("fold_to_squeeze") == (1, 1)
    assert core["pA"].count("call_vs_squeeze") == (1, 0)
    assert core["pB"].count("fold_to_squeeze") == (1, 1)
    assert core["pC"].count("squeeze") == (1, 1)
    assert core["pF"].count("fold_to_squeeze") == (0, 0)
    # s07: A opens, B calls, D squeezes; A folds, B calls
    assert special["pA"].count("fold_to_squeeze") == (1, 1)
    assert special["pB"].count("call_vs_squeeze") == (1, 1)
    assert special["pB"].count("fold_to_squeeze") == (1, 0)


def test_three_bet_sizes(core: dict[str, RawStats], special: dict[str, RawStats]) -> None:
    # (raise-to / open, in position vs opener, callers between open and 3-bet)
    assert core["pC"].three_bet_sizes == [(3.0, True, 0), (4.4, True, 1), (3.0, True, 0)]
    assert core["pA"].three_bet_sizes == [(3.25, False, 0)]  # c24 limp-reraise vs CO iso
    assert core["pF"].three_bet_sizes == [(40.0, False, 0)]  # c20 all-in
    assert core["pD"].three_bet_sizes == [(3.2, True, 0)]
    assert special["pD"].three_bet_sizes == [(4.8, True, 1)]
    assert special["pB"].three_bet_sizes == [(3.2, False, 0)]  # HU BB vs BTN is OOP


def test_depth_buckets(core: dict[str, RawStats], special: dict[str, RawStats]) -> None:
    assert set(core["pA"].by_depth) == {"60-150bb"}  # every core stack is 100bb
    assert core["pA"].by_depth["60-150bb"]["vpip"] == [24, 11]
    # s08: 20000 vs 15000 chips at bb=50 -> both 300bb effective
    assert special["pB"].by_depth[">250bb"]["three_bet"] == [1, 1]
    assert special["pA"].by_depth[">250bb"]["fold_to_three_bet"] == [1, 1]
    # s09: erin has 50bb; frank's effective stack is still 100bb
    assert special["pE"].by_depth["<=60bb"]["pfr"] == [1, 1]
    assert "<=60bb" not in special["pF"].by_depth


def test_post_missed_bb_is_live() -> None:
    from pn.profile.walk import _walk_decisions

    hand = {
        "players": [{"player_id": p} for p in ("a", "b", "c")],
        "actions": [
            {"street": "preflop", "seq": 0, "player_id": "a", "kind": "post_sb", "amount": 25},
            {"street": "preflop", "seq": 1, "player_id": "b", "kind": "post_bb", "amount": 50},
            {"street": "preflop", "seq": 2, "player_id": "c", "kind": "post_missed_bb",
             "amount": 50},
            {"street": "preflop", "seq": 3, "player_id": "c", "kind": "check", "amount": 0},
        ],
    }  # fmt: skip
    d = _walk_decisions(hand)[0]
    assert d.facing == 0 and d.pot_before == 125


def test_allin_pfa_gives_no_donk_or_probe_opportunity() -> None:
    from test_profile_fixture_build import build

    seats = [("pA", "CO"), ("pB", "BTN"), ("pE", "SB"), ("pF", "BB")]
    h = build("allin_pfa", seats, "pA r 5000; pB c; pE f; pF c; F; pF x; pB x; T; pF b 300; pB f",
              "Kd 7s 2c 9h", winners=["pA"])  # fmt: skip
    hf = walk_hand(h)
    assert hf is not None and hf.pfa == "pA"
    assert "donk_flop" not in hf.players["pF"].stats
    assert "probe_turn" not in hf.players["pF"].stats
