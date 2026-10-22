"""Hero review verdicts on synthetic heads-up fixtures (one per branch)."""

from __future__ import annotations

import pytest
from test_profile_fixture_build import build

from pn.profile.baselines import load_baselines
from pn.profile.build import build_from_walked, stats_block
from pn.profile.priors import load_priors
from pn.profile.review import judge, opportunities, review
from pn.profile.stats import RawStats, aggregate, walk_all

PRIORS, BL = load_priors(), load_baselines()
CBET = "{v} r 125; pH c; F; pH x; {v} b 85; pH f"  # villain c-bets, hero folds
CHECK_BACK = "{v} r 125; pH c; F; pH x; {v} x; T; pH x; {v} x; R; pH x; {v} x"


def _hu(i: int, villain: str, script: str) -> dict:
    return build(f"h{i}", [(villain, "BTN"), ("pH", "BB")], script.format(v=villain),
                 "Kd 7s 2c 9h 4d", winners=[villain])  # fmt: skip


def _review(hands: list[dict]) -> dict:
    walked = walk_all(hands)
    raw = aggregate(walked)
    p = build_from_walked("pH", walked, raw_all=raw, priors=PRIORS, baselines=BL)

    def stats_for(cid: str) -> dict:
        return stats_block(raw.get(cid, RawStats()), PRIORS, BL)

    rows = review("pH", p["leaks"], walked, raw, stats_for)
    return next(r for r in rows if r["stat"] == "fold_to_cbet_flop" and r["spot"] == "BB_HU")


def test_justified_vs_low_cbet_villain() -> None:
    # villain c-bets 15 of 55 flops (well below GTO 55%); hero folds to all 15
    hands = [_hu(i, "pV", CBET) for i in range(15)]
    hands += [_hu(100 + i, "pV", CHECK_BACK) for i in range(40)]
    r = _review(hands)
    assert r["direction"] == "over" and r["n"] == 15
    assert r["verdict"] == "justified", r["reason"]
    assert r["opponents"][0]["id"] == "pV" and r["opponents"][0]["sign"] == -1
    assert r["cost"] == pytest.approx(abs(r["dev_gto"]) * 15, abs=1e-3)


def test_leak_vs_cbet_every_flop_villain() -> None:
    r = _review([_hu(i, "pV", CBET) for i in range(40)])  # villain c-bets 100%
    assert r["verdict"] == "leak", r["reason"]
    assert r["opponents"][0]["sign"] == 1


def test_unknown_when_opponent_samples_small() -> None:
    r = _review([_hu(i, f"v{i}", CBET) for i in range(15)])  # 15 different villains, 1 hand each
    assert r["verdict"] == "unknown" and r["reason"].startswith("opp reads too thin")
    assert "reliable" in r["detail"]


def test_outcome_and_unmapped_stats_are_unknown() -> None:
    leak = {"stat": "wsd", "spot": "all", "dev_gto": 0.1}
    assert judge(leak, {"x": 5.0}, {}, {})["verdict"] == "unknown"
    leak = {"stat": "made_up", "spot": "all", "dev_gto": 0.1}
    assert judge(leak, {"x": 5.0}, {}, {})["verdict"] == "unknown"


def test_opportunity_weights_match_leak_n() -> None:
    hands = [_hu(i, "pV", CBET) for i in range(12)] + [_hu(50, "pW", CBET)]
    w = opportunities(walk_all(hands), "pH", "fold_to_cbet_flop", "all", {})
    assert w == {"pV": 12.0, "pW": 1.0}
    fvs = opportunities(walk_all(hands), "pH", "fold_vs_size", "flop_33", {})
    assert sum(fvs.values()) == 13.0  # 85 into 275 = 31% -> 33 bucket


def test_reasons_are_short() -> None:
    hands = [_hu(i, "pV", CBET) for i in range(15)]
    hands += [_hu(100 + i, "pV", CHECK_BACK) for i in range(40)]
    r = _review(hands)
    assert len(r["reason"]) <= 90 and r["reason"].startswith("15/15 opps vs cbet_flop below GTO")
    assert len(r["detail"]) > len(r["reason"])


def test_review_matches_positional_spots() -> None:
    hands = [_hu(i, "pV", CBET) for i in range(12)]
    walked = walk_all(hands)
    assert opportunities(walked, "pH", "fold_to_cbet_flop", "BB_HU", {}) == {"pV": 12.0}
    assert opportunities(walked, "pH", "fold_to_cbet_flop", "OOP", {}) == {"pV": 12.0}
    assert opportunities(walked, "pH", "fold_to_cbet_flop", "IP", {}) == {}
    assert opportunities(walked, "pH", "three_bet", "BB_HU vs BTN_HU", {}) == {"pV": 12.0}
