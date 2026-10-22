"""Pairwise stats on fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from pn.profile.pairwise import pairwise, pairwise_counts
from pn.profile.stats import walk_all

FIX = Path(__file__).parent / "fixtures"
WALKED = walk_all(json.loads((FIX / "hands_core.json").read_text()))


def test_counts_frank_vs_dave() -> None:
    rec = pairwise_counts(WALKED, "pF")["pD"]
    assert rec["hands_together"] == 24
    # frank facing dave's postflop bets/raises: c07 (raise, fold), c14 fold, c16 call+fold,
    # c17 call+call
    assert rec["counts"]["fold_to_their_bet"] == [6, 3]
    assert rec["counts"]["call_vs_their_bet"] == [6, 3]
    # frank's first decision facing exactly one raise by dave: c07, c14, c16, c17
    assert rec["counts"]["three_bet_vs"] == [4, 0]
    assert rec["counts"]["vpip_when_in"] == [23, 12]


def test_net_vs_definition() -> None:
    rec = pairwise_counts(WALKED, "pF")["pD"]
    # dave involved (vpip or saw flop) in c07, c10 (called), c14, c16, c17, c22
    involved = ("c07", "c10", "c14", "c16", "c17", "c22")
    assert rec["hands_b_involved"] == len(involved)
    hands = json.loads((FIX / "hands_core.json").read_text())
    expected = sum(h["net"]["pF"] for h in hands if h["hand_id"][:3] in involved)
    assert rec["net_vs"] == expected == -250 - 50 - 125 - 305 + 920 - 50


def test_posteriors() -> None:
    prior = {"vpip_when_in": (0.3, 20), "three_bet_vs": (0.06, 60),
             "fold_to_their_bet": (0.45, 15), "call_vs_their_bet": (0.45, 15)}  # fmt: skip
    vs = pairwise(WALKED, "pF", prior)
    assert set(vs) == {"pA", "pB", "pC", "pD", "pE"}
    e = vs["pD"]["fold_to_their_bet"]
    assert e["n"] == 6 and e["prior_mean"] == 0.45
