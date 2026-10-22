"""Leak collection and ordering."""

from __future__ import annotations

import json
from pathlib import Path

from pn.profile.baselines import annotate
from pn.profile.build import build_profile
from pn.profile.leaks import MIN_LEAK_N, build_leaks
from pn.profile.priors import load_priors
from pn.profile.tips import generate_tips, leak_tip

FIX = Path(__file__).parent / "fixtures"


def _obj(mean: float, lo: float, hi: float, n: int, gto: float | None) -> dict:
    return annotate({"post_mean": mean, "n": n, "k": 10, "ci90": [lo, hi], "prior_mean": mean},
                    gto)  # fmt: skip


def _pop(stat: str, spot: str) -> float | None:
    return 0.5


def test_ranking_by_dev_then_n() -> None:
    by_pos = {
        "CO": {
            "vpip": _obj(0.50, 0.45, 0.55, 200, 0.25),  # dev +0.20
            "pfr": _obj(0.05, 0.02, 0.09, 200, 0.19),  # dev -0.10
            "limp": _obj(0.30, 0.22, 0.38, 50, 0.02),  # dev +0.20, smaller n
            "steal": _obj(0.9, 0.6, 0.99, MIN_LEAK_N - 1, 0.38),  # too few opportunities
        },
        "IP": {
            "wsd": _obj(0.50, 0.40, 0.60, 80, 0.52),  # CI contains GTO: not a leak
            "af_flop": {**_obj(0.5, 0.4, 0.6, 100, 2.0), "post_mean": 0.5},  # AF 0.4-0.6 vs 2
        },
        "BTN": {"pfr": _obj(0.1, 0.05, 0.16, 40, 0.45)},  # dev -0.29
    }
    fvs = {"turn": {"150": _obj(0.80, 0.70, 0.88, 30, 0.55)}}  # dev +0.15
    leaks = build_leaks(by_pos, {}, fvs, _pop)
    order = [(lk["stat"], lk["spot"]) for lk in leaks]
    # af_flop on the p-scale: gto 2/3 vs upper edge 0.6/1.6=0.375 -> 0.29, ranks first
    assert order == [("af_flop", "IP"), ("pfr", "BTN"), ("vpip", "CO"), ("limp", "CO"),
                     ("fold_vs_size", "turn_150"), ("pfr", "CO")]  # fmt: skip
    vp = leaks[2]
    assert vp["direction"] == "over" and vp["population"] == 0.5 and vp["gto"] == 0.25
    assert {"stat", "spot", "player_freq", "population", "gto", "n", "direction",
            "exploit"} <= set(vp)  # fmt: skip
    assert leaks[1]["direction"] == "under"


def test_table_wide_cells_are_not_leaks() -> None:
    assert build_leaks({"all": {}}, {}, {}, _pop) == []
    lk = build_leaks({}, {"BB": {"CO": {"three_bet": _obj(0.02, 0.005, 0.05, 40, 0.085)}}}, {},
                     _pop)  # fmt: skip
    assert lk[0]["spot"] == "BB vs CO" and lk[0]["direction"] == "under"
    text = leak_tip(lk[0])
    assert text.startswith("3-bets 2% from BB vs CO open (n=40, CI 0-5; GTO 8%, pop 50%)")


def test_leak_tip_text() -> None:
    lk = build_leaks({}, {}, {"turn": {"150": _obj(0.80, 0.70, 0.88, 30, 0.55)}}, _pop)[0]
    text = leak_tip(lk)
    assert text.startswith("Folds 80% (n=30, CI 70-88; GTO 55%, pop 50%) to 150% turn bets")
    assert "overbet turns with air" in text
    hu = build_leaks({"BTN_HU": {"rfi": _obj(0.3, 0.2, 0.4, 50, 0.67)}}, {}, {}, _pop)[0]
    assert "from BTN (heads-up)" in leak_tip(hu)


def test_tips_draw_from_leaks_first_one_per_stat() -> None:
    by_pos = {"BTN": {"vpip": _obj(0.7, 0.6, 0.8, 50, 0.48)},
              "CO": {"vpip": _obj(0.6, 0.5, 0.7, 50, 0.30)}}  # fmt: skip
    leaks = build_leaks(by_pos, {}, {}, _pop)
    assert len(leaks) == 2
    tips = generate_tips({}, {}, {}, load_priors(), leaks=leaks)
    assert len(tips) == 1 and "GTO" in tips[0]


def test_profile_has_leaks_and_annotations() -> None:
    hands = json.loads((FIX / "hands_core.json").read_text())
    p = build_profile("pF", hands)
    for s in p["stats"].values():
        assert {"gto", "dev_pop", "dev_gto"} <= set(s)
    for lk in p["leaks"]:
        assert lk["n"] >= MIN_LEAK_N and lk["dev_gto"] != 0
    devs = [abs(lk["dev_gto"]) for lk in p["leaks"] if not lk["stat"].startswith("af_")]
    assert devs == sorted(devs, reverse=True)
    assert all(lk["spot"] != "all" for lk in p["leaks"])
