"""GTO Wizard frequencies -> preflop baselines; the chart loader's GTO Wizard layer."""

from __future__ import annotations

import pytest

from pn import ranges
from pn.profile import charts, gtowizard
from pn.profile.baselines import gto_value, load_baselines


@pytest.fixture(scope="module")
def table() -> dict:
    if not gtowizard.freqs_path().exists():
        pytest.skip("GTO Wizard freqs not present")
    return gtowizard.spot_table(gtowizard.load_freqs())


@pytest.mark.parametrize(
    "spot,raise_,call,fold",
    # totals printed by GTO Wizard itself (raw legends: "Raise 2.5 17.8%", ...)
    [("rfi|UTG", 17.8, 0.0, 82.2), ("rfi|HJ", 22.0, 0.0, 78.0), ("rfi|CO", 28.8, 0.0, 71.2),
     ("rfi|BTN", 43.3, 0.0, 56.7), ("rfi|SB", 17.7, 49.3, 32.9)],
)  # fmt: skip
def test_rfi_matches_gtowizard_totals(
    table: dict, spot: str, raise_: float, call: float, fold: float
) -> None:
    f = table[spot]
    assert 100 * f["raise"] == pytest.approx(raise_, abs=0.15)
    assert 100 * f["call"] == pytest.approx(call, abs=0.15)
    assert 100 * f["fold"] == pytest.approx(fold, abs=0.15)


def test_reach_weighting() -> None:
    # opener raises AA always and 72o never; facing a 3bet it folds 72o (out of range)
    freqs = {
        "100|rfi|UTG": {"cells": {"AA": {"R": 100}, "72o": {"F": 100}}},
        "100|vs_3bet|UTG|HJ": {"cells": {"AA": {"R": 50, "C": 50}, "72o": {"F": 100, "out": 1}}},
        "100|vs_4bet|CO|BTN": {"cells": {"AA": {"A": 100}}},  # no vs_open spot: skipped
    }
    t = gtowizard.spot_table(freqs)
    assert t["vs_3bet|UTG|HJ"] == {"raise": 0.5, "call": 0.5, "fold": 0.0, "check": 0.0}
    assert "vs_4bet|CO|BTN" not in t
    assert t["rfi|UTG"]["raise"] == pytest.approx(6 / 18)  # AA 6 combos vs 72o 12


def test_reach_chain_5bet_6bet() -> None:
    # AA: opens 100%, 4-bets 50% of the time, then faces a 5-bet. KK: opens 100%, never 4-bets.
    freqs = {
        "100|rfi|UTG": {"cells": {"AA": {"R": 100}, "KK": {"R": 100}}},
        "100|vs_3bet|UTG|BB": {"cells": {"AA": {"R": 50, "C": 50}, "KK": {"C": 100}}},
        "100|vs_5bet|UTG|BB": {"cells": {"AA": {"A": 100}, "KK": {"F": 100, "out": 1}}},
        "100|vs_open|UTG|BB": {"cells": {"AA": {"R": 100}, "KK": {"R": 50, "C": 50}}},
        "100|vs_4bet|UTG|BB": {"cells": {"AA": {"R": 100}, "KK": {"F": 100}}},
        "100|vs_6bet|UTG|BB": {"cells": {"AA": {"C": 100}, "KK": {"F": 100, "out": 1}}},
        "100|vs_5bet|CO|BTN": {"cells": {"AA": {"A": 100}}},  # no vs_3bet|CO|BTN: skipped
    }
    t = gtowizard.spot_table(freqs)
    # only AA (reach 1.0 * 0.5) faces the 5-bet; KK's reach is 0 -> its fold must not count
    assert t["vs_5bet|UTG|BB"]["raise"] == 1.0 and t["vs_5bet|UTG|BB"]["fold"] == 0.0
    assert t["vs_6bet|UTG|BB"]["call"] == 1.0
    assert "vs_5bet|CO|BTN" not in t
    pre = gtowizard.preflop_baselines(freqs)
    assert pre["five_bet"]["by_position"]["BB"] == pytest.approx(t["vs_4bet|UTG|BB"]["raise"], abs=1e-4)
    assert pre["six_bet"]["by_position"]["LJ"] == 1.0
    assert pre["fold_to_six_bet"]["by_position"]["BB"] == 0.0
    assert gtowizard.situation_key(("vs_5bet", "LJ", "BB")) == "vs_5bet|UTG|BB"
    assert gtowizard.situation_key(("vs_6bet", "LJ", "BB")) == "vs_6bet|UTG|BB"
    assert gtowizard.situation_freq(t, "six_bet", ("vs_5bet", "LJ", "BB")) == 1.0
    # AA (reach 1.0) 5-bets, KK (reach 0.5) folds: 6 / (6 + 3) combos
    assert gtowizard.situation_freq(t, "five_bet", ("vs_4bet", "LJ", "BB")) == pytest.approx(2 / 3)
    assert gtowizard.situation_freq(t, "fold_to_six_bet", ("vs_6bet", "LJ", "BB")) == 0.0


def test_unknown_cells_ignored() -> None:
    f = gtowizard.spot_freqs({"cells": {"AA": {"R": 100}, "KK": {"?": 100}}})
    assert f["raise"] == 1.0


def test_preflop_baselines_and_merge(table: dict) -> None:
    pre = gtowizard.preflop_baselines(gtowizard.load_freqs())
    assert pre["rfi"]["by_position"]["LJ"] == pytest.approx(0.178, abs=0.002)  # their UTG
    assert pre["limp"]["by_position"]["SB"] == pytest.approx(0.493, abs=0.002)
    bb = pre["cold_call"]["by_position"]["BB"]
    assert 0.25 < bb < 0.45 and 0.05 < pre["three_bet"]["by_position"]["BB"] < 0.15
    assert 0.4 < pre["fold_to_three_bet"]["default"] < 0.65
    old = {"stats": {"rfi": {"default": 0.3, "by_position": {"UTG": 0.12, "LJ": 0.2}}}}
    merged = gtowizard.merge_into(old, pre, "test")
    assert merged["stats"]["rfi"]["by_position"]["UTG"] == 0.12  # 8-max seat kept
    assert merged["stats"]["rfi"]["by_position"]["LJ"] == pre["rfi"]["by_position"]["LJ"]
    assert old["stats"]["rfi"]["by_position"]["LJ"] == 0.2  # input not mutated


def test_committed_baselines_use_gtowizard() -> None:
    bl = load_baselines()
    assert bl["_meta"]["preflop_source"].startswith("gtowizard")
    assert gto_value(bl, "rfi", "BTN") == pytest.approx(0.433, abs=0.002)


def test_chart_loader_gtowizard_layer() -> None:
    if not (charts.charts_dir() / "gtowizard_100bb.txt").exists():
        pytest.skip("GTO Wizard charts not present")
    # open / 3bet / call / 4bet / call_3bet are PokerCoaching's first (test_profile_charts_pc.py):
    # the GTO Wizard layer is checked directly here, and through chart_range on PC gaps below.
    g = charts.gtow_range("6max", "open", "LJ")
    assert g == ranges.parse(charts.chart_entries("gtowizard_100bb.txt")["open UTG"])
    assert charts.gtow_range("8max", "5bet", "UTG1", "UTG") is None  # 8-max UTG1: hand-written
    assert charts.chart_range("8max", "5bet", "UTG1", "UTG") == ranges.parse(
        charts.chart_entries("common.txt")["5bet vs4bet EP"]
    )
    assert charts.gtow_range("hu", "open", "BTN") is None
    deep = charts.gtow_range("6max", "open", "BTN", depth_bb=220)
    assert deep == ranges.parse(charts.chart_entries("gtowizard_200bb.txt")["open BTN"])
    assert charts.gtow_range("6max", "4bet", "CO", "BTN", depth_bb=220) == ranges.parse(
        charts.chart_entries("gtowizard_200bb.txt")["4bet CO vs3bet BTN"]
    )
    # BB facing SB's 200bb 6-bet shove was scraped 2026-10-16: a 200bb request uses it
    assert charts.chart_range("6max", "call_6bet", "BB", "SB", depth_bb=220) == ranges.parse(
        charts.chart_entries("gtowizard_200bb.txt")["call BB vs6bet SB"]
    )
    assert charts.chart_range("6max", "6bet", "LJ", "BB") == ranges.parse(
        charts.chart_entries("gtowizard_100bb.txt")["6bet UTG vs5bet BB"]
    )
