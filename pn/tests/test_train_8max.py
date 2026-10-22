"""Plan 16 P5: 8-max / 200bb trainer sessions. Heads-up flops of aliased 8-max lines are dealt
and graded from the 6-max library; the GTO Wizard walk covers 8-max (UTG / UTG1 from the shifted
PokerCoaching charts), so leaks, importance and drill pins work at table 8; `dealable` tells the
performance page which spots a drill can deal; flops without a board end with a clear detail."""

from __future__ import annotations

import random
from pathlib import Path

import flop_seed
import pytest
from fake_tree import fake_runner
from fastapi.testclient import TestClient

from pn import ranges
from pn.profile import charts
from pn.solve import SolveCache
from pn.train import Config, Trainer, TrainError, drills
from pn.train import flop_library as flib
from pn.train import library_alias as la
from pn.train import preflop as pre
from pn.train.leaks import Leaks, _gtow_freqs, gtow_walk, pc_spot_table
from pn.train.session import _no_board

POST = "post|srp|ip|flop|first"


@pytest.fixture(autouse=True)
def _need_data() -> None:
    if not pre.has_freqs() or not (charts.charts_dir() / charts.PC_FILE).exists():
        pytest.skip("GTO Wizard frequencies or PokerCoaching charts not present")


@pytest.fixture
def root(flop_template: Path, tmp_path: Path) -> Path:
    """The fake 6-max 100bb library with the 8-max 100bb aliases."""
    flop_seed.copy_into(flop_template, tmp_path / "cache")
    la.run(flib.FlopIndex(tmp_path / "cache"), 8, 100, sample=1)
    return tmp_path / "cache"


def _passive(view: dict) -> str:
    toks = [o["token"] for o in view["legal_actions"]]
    return next(t for t in ("check", "call", "fold") if t in toks)


# ------------------------------------------------------------------ the walk


def test_8max_walk_extends_the_6max_chains_by_the_ep_folds() -> None:
    fr = _gtow_freqs()
    w6, w8 = gtow_walk(fr, 6, 100), gtow_walk(fr, 8, 100)
    t6, t8 = pc_spot_table(6), pc_spot_table(8)
    assert all(t8[k] == v for k, v in t6.items())  # LJ..BB charts: the 6-max ones
    p = t8["rfi|EP:UTG"]["fold"] * t8["rfi|EP:UTG1"]["fold"]  # both EP seats fold
    assert 0.5 < p < 0.95
    first = [k for k in w6.freq if k.split("|")[2] in ("rfi", "vs_open", "vs_limp")]
    assert len(first) > 20
    for k in first:  # hero seat uniform over 8 seats, reached when UTG and UTG1 folded
        assert w8.freq[k] == pytest.approx(w6.freq[k] * 6 / 8 * p), k
    assert w8.freq["pf|UTG|rfi|-"] == pytest.approx(1 / 8)
    assert w8.freq["pf|UTG1|vs_open|UTG"] > 0 and w8.freq["pf|BTN|vs_open|UTG1"] > 0
    assert w8.formations["UTG vs BB - SRP"] > 0 and "post|srp|ip" in w8.post_lines
    assert w8.post_lines["post|3bp|oop"]  # EP open, 3-bet behind, called


def test_an_ep_line_samples_hands_from_the_shifted_charts() -> None:
    w8 = gtow_walk(_gtow_freqs(), 8, 100)
    line = w8.lines["pf|HJ|vs_open|UTG"]
    utg = next(s for s in line.steps if s.position == "UTG")
    assert utg.src == ("pc", ("8max", "rfi", "UTG", None), ("R", "A"))
    opens = charts.chart_range("8max", "open", "UTG")  # = 9-handed UTG1 (Plan 16 P1)
    rng = random.Random(5)
    for _ in range(20):
        cards = drills.sample_hands(line, rng, _gtow_freqs())["UTG"]
        assert opens.get(ranges.canonical_combo("".join(cards)), 0) > 0


# ------------------------------------------------------------------ sessions


def test_8max_hands_reach_graded_flops_through_aliases(root: Path) -> None:
    t = Trainer(SolveCache(root, runner=fake_runner), background=False)
    v = t.new(Config(table=8, seed=21, hero_seat="BB"))
    aliased = ended = 0
    for _ in range(150):
        while v["result"] is None:
            h = t.get(v["sid"]).hand
            if v["street"] == "flop" and len(h.state.in_hand()) == 2 and v["legal_actions"]:
                m = flib.matchup_of(h.state, "8max", 100)
                if t.flops.alias_of(m.mid) is not None:
                    assert v["solve"]["status"] == "ready" and v["solve"]["backend"] == "texas"
                    v = t.act(v["sid"], _passive(v))
                    fb = v["hand_feedback"][-1]
                    assert fb["street"] == "flop" and fb["origin"] == "solve"
                    aliased += 1
                    continue
            v = t.act(v["sid"], _passive(v))
        if v["solve"]["note"] == "no_library_solve":  # an EP line: not solved yet, or a limp
            d = v["solve"]["detail"]
            assert (
                d.startswith(("no solved flop for ", "limped pots are not solved at 8-max"))
                and "UTG" in d
            )
            ended += 1
        if aliased >= 2 and ended:
            break
        v = t.next(v["sid"])
    assert aliased >= 2 and ended


def test_no_board_detail_flags_limped_pots_outside_6max_100bb() -> None:
    m6 = {m.pot: m for m in la.lib_matchups(6, 200)}
    assert _no_board(Config(table=6, depth=200), m6["limped"]).startswith(
        "limped pots are not solved at 6-max 200bb"
    )
    assert _no_board(Config(table=6, depth=200), m6["srp"]).startswith("no solved flop for srp")
    assert _no_board(Config(), None) is None
    m8 = next(m for m in la.lib_matchups(8, 100) if m.pot == "limped" and la.ep_acts(m))
    assert "8-max 100bb" in _no_board(Config(table=8), m8)


def test_8max_drills_deal_through_aliases_and_dealable_says_which(
    root: Path, tmp_path: Path
) -> None:
    t = Trainer(SolveCache(root, runner=fake_runner), background=False, leaks=Leaks(None))
    ok = t.dealable(8, 100, [POST, "pf|UTG|rfi|-", "pf|BTN|vs_open|UTG1", "pf|CO|vs_4bet+|UTG"])
    assert ok == {POST: True, "pf|UTG|rfi|-": True, "pf|BTN|vs_open|UTG1": True,
                  "pf|CO|vs_4bet+|UTG": False}  # fmt: skip
    assert t.dealable(8, 200, [POST, "pf|UTG|rfi|-"]) == {POST: False, "pf|UTG|rfi|-": True}
    assert t.dealable(6, 100, [POST, "nonsense"]) == {POST: True, "nonsense": False}
    with pytest.raises(TrainError):
        t.dealable(9, 100, [POST])
    bare = Trainer(SolveCache(tmp_path / "bare", runner=fake_runner), background=False,
                   leaks=Leaks(None))  # fmt: skip
    assert bare.dealable(8, 100, [POST]) == {POST: False}  # no boards, no alias
    v = t.new(Config(table=8, seed=8, drill_share=0.0, drill_spot=POST))
    assert v["drill"]["spot"] == POST
    line = t.get(v["sid"]).hand.drill.line
    for _ in range(12):  # hero follows the line, villains steer to hero's first flop decision
        if v["street"] == "flop" or v["result"] is not None:
            break
        st = t.get(v["sid"]).hand.state
        kind = drills.preflop_step(t.get(v["sid"]).hand.drill, st, st.to_act)
        toks = [o["token"] for o in v["legal_actions"]]
        v = t.act(
            v["sid"],
            next(x for x in toks if x.startswith("raise")) if kind == "raise" else _passive(v),
        )
    assert line.hero == v["hero"]["position"]
    assert v["street"] == "flop" and v["solve"]["status"] == "ready"


def test_dealable_route(root: Path, tmp_path: Path) -> None:
    from pn.web.app import create_app

    c = TestClient(create_app(tmp_path / "none.sqlite", tmp_path))
    c.app.state.trainer = Trainer(SolveCache(root, runner=fake_runner), background=False,
                                  leaks=Leaks(None))  # fmt: skip
    r = c.get("/api/train/dealable", params=[("table", 8), ("depth", 100), ("spot", POST),
                                             ("spot", "pf|UTG|rfi|-")])  # fmt: skip
    assert r.status_code == 200
    assert r.json() == {"table": 8, "depth": 100, "dealable": {POST: True, "pf|UTG|rfi|-": True}}
    assert c.get("/api/train/dealable", params={"table": 7}).status_code == 400
