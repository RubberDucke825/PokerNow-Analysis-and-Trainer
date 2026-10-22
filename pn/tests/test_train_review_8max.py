"""Plan 16 trainer review (2026-10-15): the 8-max walk does not count cold calls of an EP open's
3-bet (no chart: they would be graded against the opener's call-vs-3bet chart), and ingested
7- / 9-handed hands read the PokerCoaching 9-handed seat with the same players behind."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import flop_seed
import pytest
from fake_tree import fake_runner

from pn import ranges
from pn.ev import grade
from pn.profile import charts
from pn.solve import SolveCache
from pn.train import Config, Trainer
from pn.train import flop_library as flib
from pn.train import library_alias as la
from pn.train import preflop as pre
from pn.train.leaks import Leaks, _gtow_freqs, gtow_walk, pc_spot_table


@pytest.fixture(autouse=True)
def _need_data() -> None:
    if not pre.has_freqs() or not (charts.charts_dir() / charts.PC_FILE).exists():
        pytest.skip("GTO Wizard frequencies or PokerCoaching charts not present")


# ------------------------------------------------------------------ cold calls of EP 3-bets


def test_8max_walk_has_no_cold_call_of_an_ep_opens_3bet() -> None:
    fr = _gtow_freqs()
    w6, w8 = gtow_walk(fr, 6, 100), gtow_walk(fr, 8, 100)
    t8 = pc_spot_table(8)
    p = t8["rfi|EP:UTG"]["fold"] * t8["rfi|EP:UTG1"]["fold"]
    cold6 = {k for k in w6.freq if k.startswith("pf|") and "|cold|" in k}
    cold8 = {k for k in w8.freq if k.startswith("pf|") and "|cold|" in k}
    assert len(cold6) == 10 and cold8 == cold6  # no pf|*|cold|UTG1 or pf|*|cold|LJ
    for k in cold6:  # reached only when UTG and UTG1 folded: the 6-max chains
        assert w8.freq[k] == pytest.approx(w6.freq[k] * 6 / 8 * p), k


def test_ep_cold_spots_are_not_dealable(flop_template: Path, tmp_path: Path) -> None:
    flop_seed.copy_into(flop_template, tmp_path / "cache")
    la.run(flib.FlopIndex(tmp_path / "cache"), 8, 100, sample=1)
    t = Trainer(SolveCache(tmp_path / "cache", runner=fake_runner), background=False,
                leaks=Leaks(None))  # fmt: skip
    ep = ["pf|BB|cold|UTG1", "pf|CO|cold|LJ", "pf|LJ|cold|UTG1"]
    assert t.dealable(8, 100, [*ep, "pf|BB|cold|HJ"]) == {
        **dict.fromkeys(ep, False), "pf|BB|cold|HJ": True}  # fmt: skip


# ------------------------------------------------------------------ 7- / 9-handed seats


def test_ep_seats_read_the_9handed_chart_with_the_same_players_behind() -> None:
    assert charts.pc_key("8max", "open", "UTG", players=9) == "open UTG"
    assert charts.pc_key("8max", "open", "UTG1", players=9) == "open UTG1"
    assert charts.pc_key("8max", "open", "UTG2", players=9) == "open UTG2"
    assert charts.pc_key("8max", "open", "UTG", players=8) == "open UTG1"
    assert charts.pc_key("8max", "open", "UTG1", players=8) == "open UTG2"
    assert charts.pc_key("8max", "open", "UTG", players=7) == "open UTG2"
    assert charts.pc_key("8max", "open", "UTG") == "open UTG1"  # the trainer: 8 seats
    assert charts.pc_key("8max", "3bet", "BTN", "UTG", players=7) == "3bet BTN vs UTG2"
    assert charts.pc_key("8max", "3bet", "UTG2", "UTG1", players=9) == "3bet UTG2 vs UTG1"
    assert charts.pc_key("8max", "open", "LJ", players=7) == "open LJ"
    assert charts.pc_key("6max", "open", "UTG", players=6) == "open LJ"
    nine = ranges.parse(charts.chart_entries(charts.PC_FILE)["open UTG"])
    assert charts.chart_range("8max", "open", "UTG", players=9) == nine


def test_review_grades_ingested_ep_seats_by_players_behind() -> None:
    def kinds(n: int, situ: tuple, combo: str) -> dict | None:
        hand = {"players": [{}] * n}
        return grade.pc_kinds(hand, SimpleNamespace(situ=situ, combo=combo))

    wide = charts.pc_range("8max", "open", "UTG", players=7)  # 9h UTG2
    utg1 = charts.pc_range("8max", "open", "UTG1", players=9)
    combo = next(c for c, w in wide.items() if w and not utg1.get(c))
    assert kinds(7, ("rfi", "UTG"), combo) == {"raise": 1.0}  # 7-handed UTG = 9h UTG2: opens
    assert kinds(9, ("rfi", "UTG1"), combo) == {"fold": 1.0}  # 9h UTG1 folds it
    assert kinds(8, ("rfi", "UTG"), combo) == {"fold": 1.0}  # 8-handed UTG = 9h UTG1


# ------------------------------------------------------------------ drill hands


@pytest.mark.parametrize("table", [6, 8])
def test_drill_hero_hand_follows_heros_own_raises(table: int, tmp_path: Path) -> None:
    """The web Trainer has no `freqs`: its drills must still sample hero's GTO Wizard steps
    (they were uniform, so hero faced a 4-bet holding 42o after 3-betting)."""
    from pn import ranges as rg

    t = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False, leaks=Leaks(None))
    three = pre.gtow_spot("vs_open|BTN|BB", 100)[1]  # BB 3-bets BTN: hero's own step
    for seed in range(6):
        v = t.new(Config(table=table, seed=seed, drill_share=0.0,
                         drill_spot="pf|BB|vs_4bet+|BTN"))  # fmt: skip
        h = t.get(v["sid"]).hand
        assert h.drill is not None and h.drill.line.hero == "BB"
        cls = rg.combo_class(rg.canonical_combo("".join(v["hero"]["cards"])))
        d = pre.cell_dist(three, cls)
        assert d.get("R", 0) + d.get("A", 0) > 0, (seed, cls)
