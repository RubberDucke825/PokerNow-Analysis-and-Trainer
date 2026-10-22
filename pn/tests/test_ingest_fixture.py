"""End-to-end checks against the real 298-hand PokerNow log + ledger."""

import json
from collections import Counter
from pathlib import Path

import pytest

from pn.ingest.parse import parse_messages, session_events
from pn.ingest.validate import check_hand, ledger_crosscheck, read_ledger, stack_continuity

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures"
GAME = "pglSAMPLE0000000000000000"


@pytest.fixture(scope="module")
def raw():
    return json.loads((FIX / f"pokernow_{GAME}.json").read_text())


@pytest.fixture(scope="module")
def hands(raw):
    return parse_messages(raw, game_id=GAME, fetched_at="2026-10-01T00:00:00+00:00")


def test_parses_exactly_298_contiguous_hands(hands):
    assert len(hands) == 298
    assert [h["hand_no"] for h in hands] == list(range(1, 299))
    assert len({h["hand_id"] for h in hands}) == 298


def test_flagged_hands_reported_and_clean_hands_net_zero(hands):
    flagged = [h for h in hands if {"pot_mismatch", "incomplete"} & set(h["flags"])]
    print(f"\nflagged hands: {len(flagged)}")
    for h in flagged:
        print(f"  #{h['hand_no']} {h['flags']} {h['issues']}")
    print("flag counts:", dict(Counter(f for h in hands for f in h["flags"])))
    assert flagged == []
    for h in hands:
        assert sum(h["net"].values()) == 0, h["hand_no"]
        assert check_hand(h) == [], (h["hand_no"], check_hand(h))
        assert h["issues"] == []


def test_run_it_twice_hands_have_two_runs(hands):
    rit = [h for h in hands if "run_it_twice" in h["flags"]]
    assert [h["hand_no"] for h in rit] == [55, 75, 106, 142, 194]
    for h in rit:
        assert len(h["runs"]) == 2
        assert all(r["board"]["river"] for r in h["runs"])
        assert h["board"] == h["runs"][0]["board"]
    h55 = next(h for h in rit if h["hand_no"] == 55)
    assert h55["runs"][1]["board"]["flop"] == h55["runs"][0]["board"]["flop"]
    assert (h55["runs"][0]["board"]["river"], h55["runs"][1]["board"]["river"]) == ("3h", "3d")


def test_showdown_cards_recovered_for_every_shows_line(raw, hands):
    shows_lines = sum(1 for m in raw if " shows a " in m["msg"])
    assert shows_lines == 134
    shown = [s for h in hands for s in h["showdowns"] if s["shown"]]
    assert len(shown) == 133  # one player showed twice in the same hand
    h55 = next(h for h in hands if h["hand_no"] == 55)
    cards = {p["name"]: p["cards"] for p in h55["players"]}
    assert cards["kim"] == ["Ts", "Kd"] and cards["lou"] == ["Ks", "Ac"]
    for h in hands:
        for p in h["players"]:
            if p["cards"]:
                assert len(p["cards"]) == 2 and p["cards_source"] == "showdown"


def test_special_blind_hands(hands):
    by_no = {h["hand_no"]: h for h in hands}
    assert "dead_sb" in by_no[80]["flags"] and "SB" not in {
        p["position"] for p in by_no[80]["players"]
    }
    assert by_no[65]["button_seat"] is None and "dead_button" in by_no[65]["flags"]
    kinds = [a["kind"] for a in by_no[86]["actions"] if a["player_id"] == "hero000001"]
    assert kinds[:2] == ["post_missing_sb", "post_missed_bb"]
    assert [p["amount"] for p in by_no[143]["pots"]] == [478, 3600]


def test_ledger_crosscheck_exact(hands):
    rows = ledger_crosscheck(hands, read_ledger(FIX / f"ledger_{GAME}.csv"))
    print("\nledger deltas:", {r["name"]: r["delta"] for r in rows})
    assert len(rows) == 9
    assert all(r["delta"] == 0 for r in rows)


def test_stack_continuity_breaks_all_explained(raw, hands):
    breaks = stack_continuity(hands, session_events(raw))
    assert len(breaks) == 9
    assert all(b["explained_by"] for b in breaks), [b for b in breaks if not b["explained_by"]]
