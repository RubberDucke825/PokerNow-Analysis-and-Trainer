"""2026-10-07: GTO Wizard chain frequencies for preflop and heads-up postflop spots (no library
root), postflop drills that need a solved board in the flop index, and the solve note of hands
that never reach a heads-up flop."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pn import ranges
from pn.profile import charts, gtowizard
from pn.solve import SolveCache
from pn.solve import flops as fl
from pn.solve.request import TABLE_SEATS
from pn.train import Config, Trainer, leaks, spots
from pn.train import flop_library as flib
from pn.train import preflop as pre
from pn.train import suitmap as sm
from pn.train.drills import flop_ranges
from pn.train.leaks import FIRST_FALLBACK, Leaks, gtow_walk

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from fake_tree import fake_runner
from flop_seed import texas_runner

ORDER = TABLE_SEATS[6]


def _cells(**pct: float) -> dict:
    return {"cells": {cls: dict(pct) for cls in ("AA", "72o")}}


# Only the BTN opens; the SB and BB defend. Uniform cells, so reach weighting changes nothing.
FAKE = {
    "100|rfi|UTG": _cells(F=100), "100|rfi|HJ": _cells(F=100), "100|rfi|CO": _cells(F=100),
    "100|rfi|BTN": _cells(R=50, F=50), "100|rfi|SB": _cells(F=100),
    "100|vs_open|BTN|SB": _cells(F=80, C=10, R=10),
    "100|vs_open|BTN|BB": _cells(F=40, C=40, R=20),
    "100|vs_3bet|BTN|SB": _cells(F=50, C=50),
    "100|vs_3bet|BTN|BB": _cells(F=60, C=30, R=10),
    "100|cold|BTN|SB|BB": _cells(F=90, C=10),
    "100|vs_4bet|BTN|BB": _cells(F=50, C=50),
}  # fmt: skip


def test_heads_up_flop_chains() -> None:
    fm = gtow_walk(FAKE, 6, 100, pokercoaching=False)
    srp_bb = 0.5 * 0.8 * 0.4  # BTN opens, SB folds, BB calls
    srp_sb = 0.5 * 0.1 * 0.4  # SB calls, BB folds with its vs_open fold share (no squeeze node)
    assert fm.freq["pf|BB|vs_open|BTN"] == pytest.approx(0.5 * 0.8 / 6)
    assert fm.freq["post|srp|oop"] == pytest.approx((srp_bb + srp_sb) / 6)
    assert fm.freq["post|srp|ip"] == pytest.approx((srp_bb + srp_sb) / 6)  # the BTN both times
    p3_bb = 0.5 * 0.8 * 0.2 * 0.3  # BB 3-bets, BTN calls
    p3_sb = 0.5 * 0.1 * 0.9 * 0.5  # SB 3-bets, BB folds cold, BTN calls
    cold = 0.5 * 0.1 * 0.1 * 0.5  # SB 3-bets, BB cold-calls, the BTN folds (vs_3bet fold)
    assert fm.freq["post|3bp|oop"] == pytest.approx((p3_bb + p3_sb + cold) / 6)  # BB, SB, SB
    assert fm.freq["post|3bp|ip"] == pytest.approx((p3_bb + p3_sb + cold) / 6)  # BTN, BTN, BB
    assert fm.freq["post|4bp+|oop"] == pytest.approx(0.5 * 0.8 * 0.2 * 0.1 * 0.5 / 6)
    assert "post|limped|ip" not in fm.freq  # the SB never limps here
    best = fm.post_lines["post|srp|oop"]
    assert [ln.hero for ln in best] == ["BB", "SB"]  # highest reach first
    assert [(s.position, s.kind) for s in best[0].steps] == [
        ("LJ", "fold"), ("HJ", "fold"), ("CO", "fold"), ("BTN", "raise"), ("SB", "fold"),
        ("BB", "call")]  # fmt: skip
    assert fm.lines["post|srp|oop"] == best[0]
    cold_line = next(ln for ln in fm.post_lines["post|3bp|ip"] if ln.hero == "BB")
    assert [(s.position, s.kind) for s in cold_line.steps][-3:] == [
        ("SB", "raise"), ("BB", "call"), ("BTN", "fold")]  # fmt: skip


def test_limped_pot_chain() -> None:
    freqs = {**FAKE, "100|rfi|SB": _cells(C=60, F=40), "100|vs_limp|SB|BB": _cells(X=75, R=25)}
    fm = gtow_walk(freqs, 6, 100, pokercoaching=False)
    p = 0.5 * 0.6 * 0.75  # folds to the SB (BTN folds half the time), limp, BB checks
    assert fm.freq["post|limped|oop"] == pytest.approx(p / 6)  # SB
    assert fm.freq["post|limped|ip"] == pytest.approx(p / 6)  # BB
    assert fm.lines["post|limped|ip"].hero == "BB"


def _pc_share(key: str) -> float:
    """Share of all 1326 combos in a PokerCoaching chart."""
    return sum(charts.pc_range("6max", *key).values()) / ranges.N_COMBOS


def test_pokercoaching_charts_set_the_shares_where_they_exist() -> None:
    table = leaks.pc_spot_table()
    for cells in (pre.pc_class_dists("6max", "rfi", "BTN", None)[0],
                  pre.pc_class_dists("6max", "vs_open", "BB", "BTN")[0]):  # fmt: skip
        assert all(list(d.values()) == [1.0] for d in cells.values())  # pure: one action per class
    assert table["rfi|BTN"]["raise"] == pytest.approx(_pc_share(("open", "BTN")))
    assert table["vs_open|BTN|BB"]["call"] == pytest.approx(_pc_share(("call", "BB", "BTN")))
    fm = gtow_walk(FAKE, 6, 100)  # FAKE: only the BTN opens, BB vs BTN F40 C40 R20
    folds = 1.0
    for p in ("UTG", "HJ"):  # LJ and HJ fold with their PokerCoaching fold share
        folds *= table[f"rfi|{p}"]["fold"]
    assert fm.freq["pf|CO|rfi|-"] == pytest.approx(folds / 6)
    btn_open = folds * table["rfi|CO"]["fold"] * table["rfi|BTN"]["raise"]
    assert fm.freq["pf|BB|vs_open|BTN"] == pytest.approx(btn_open * table["vs_open|BTN|SB"]["fold"] / 6)


def test_gto_wizard_fills_spots_pokercoaching_does_not_chart() -> None:
    table = leaks.pc_spot_table()
    assert not any(k.split("|")[0] in ("vs_limp", "cold", "vs_4bet") for k in table)
    bb_vs_4bet = spots.spot_key({"street": "preflop", "position": "BB",
                                 **spots.preflop_fields("BB", ["BTN", "BB", "BTN"], 0)})  # fmt: skip
    for call in (50, 20):  # BB calls the BTN 4-bet with the GTO Wizard call share
        freqs = {**FAKE, "100|vs_4bet|BTN|BB": _cells(F=100 - call, C=call)}
        fm = gtow_walk(freqs, 6, 100)
        assert fm.freq[bb_vs_4bet] > 0
        assert fm.freq["post|4bp+|oop"] == pytest.approx(fm.freq[bb_vs_4bet] * call / 100)


def test_postflop_frequency_is_reach_times_conditional(tmp_path: Path) -> None:
    model = Leaks(None, library=lambda t, d: None, cache_root=tmp_path, freqs=FAKE)
    freq, fm = model.frequencies(None, 6, 100, 1000)
    # no solved flop, no observation: flop `first` falls back to 1.0, nothing else is listed
    assert freq["post|srp|oop|flop|first"] == pytest.approx(fm.freq["post|srp|oop"] * FIRST_FALLBACK)
    assert not any(k.startswith("post|") and not k.endswith("|flop|first") for k in freq)
    # a solved heads-up srp flop prices both sides from its two seats
    from pn.solve import build_spot
    from pn.solve.request import new_request, parse_board

    SolveCache(tmp_path, runner=fake_runner).solve(build_spot(new_request(
        ["BTN", "BB"], 100, board=parse_board("Kd7s2c"), line="srp")))  # fmt: skip
    _, cond = model.solver_map(6, 100)
    freq, fm = model.frequencies(None, 6, 100, 1000)
    for key, c in cond.items():
        assert freq[key] == pytest.approx(fm.freq["|".join(key.split("|")[:3])] * c)
    assert any(k.startswith("post|srp|oop|") for k in cond)
    assert any(k.startswith("post|srp|ip|") for k in cond)
    rows = model.rows(None, 6, 100, 1000)
    assert any(r["spot"].startswith("post|") and r["freq"] > 0 for r in rows)


@pytest.fixture(scope="module")
def real_freqs() -> dict:
    if not gtowizard.freqs_path().exists():
        pytest.skip("GTO Wizard freqs missing")
    return gtowizard.load_freqs()


def test_real_chains_replay_into_library_matchups(real_freqs: dict) -> None:
    fm = gtow_walk(real_freqs, 6, 100)
    lines = {m.line for m in flib.matchups(ORDER, 100, "6max", sb=50, bb=100)}
    assert 0.3 < 3 * sum(v for k, v in fm.freq.items() if k.startswith("post|")) < 0.5
    for pk, ls in fm.post_lines.items():
        for ln in ls:
            st = flib.replay(ORDER, 100, [(s.position, s.kind) for s in ln.steps], sb=50, bb=100)
            m = flib.matchup_of(st, "6max", 100) if st is not None else None
            assert m is not None and m.line in lines, (pk, ln.steps)
            assert f"post|{m.pot}|" in pk + "|"


def _seed_one(root: Path, line: str, board: str = "Kc9c8d") -> None:
    """A flop index with one solved board for the matchup with this preflop line."""
    cache, index = SolveCache(root, runner=texas_runner), flib.FlopIndex(root)
    m = next(m for m in flib.matchups(ORDER, 100, "6max", sb=50, bb=100) if m.line == line)
    _, spot, key = flib.flop_spot(m, tuple(fl.flop_cards(board)))
    cache.solve(spot, "texas")
    index.record(m, board, key, 1)


class OneSpot(Leaks):
    def __init__(self, spot: str, **kw) -> None:
        super().__init__(None, **kw)
        self.spot = spot

    def importance(self, conn, table, depth, window, **kw):
        row = {"spot": self.spot, "label": spots.label(self.spot), "weight": 1.0}
        return {"spots": [row], "formations": [], "textures": [], "params": {}}


def _play_to_flop(t: Trainer, v: dict) -> dict:
    while not v["hand_over"] and v["street"] == "preflop":
        toks = [a["token"] for a in v["legal_actions"]]
        v = t.act(v["sid"], "call" if "call" in toks else "check")
    return v


def test_postflop_drill_uses_a_line_with_a_solved_board(real_freqs: dict, tmp_path: Path) -> None:
    spot = "post|srp|oop|flop|first"
    leaks = OneSpot(spot, library=lambda t, d: None, cache_root=tmp_path, freqs=real_freqs)
    bare = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False, leaks=leaks)
    assert bare.new(Config(seed=4, drill_share=1.0, hero_seat="BB"))["drill"] is None
    # only CO-BB is solved: the drill takes that line, not the more frequent BTN-BB one
    _seed_one(tmp_path, "CO raise, BB call")
    t = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False,
                leaks=OneSpot(spot, library=lambda tb, d: None, cache_root=tmp_path,
                              freqs=real_freqs))  # fmt: skip
    assert leaks.lines(6, 100, spot)[0].steps != t._drill_line(Config(), spot).steps
    v = t.new(Config(seed=4, drill_share=1.0, hero_seat="BB"))
    assert v["drill"] == {"spot": spot, "label": spots.label(spot)}
    assert v["hero"]["position"] == "BB"
    v = _play_to_flop(t, v)
    perm = t.get(v["sid"]).hand.perm  # the library board, dealt under a suit permutation
    assert sm.permute_cards(("Kc", "9c", "8d"), perm) == tuple(v["board"])
    assert v["solve"]["status"] == "ready"
    assert [h["position"] for h in v["history"] if h["token"] != "fold"] == ["CO", "BB"]
    assert v["to_act"] == "BB"
    v = t.act(v["sid"], v["legal_actions"][0]["token"])
    fb = v["feedback"]  # hero's hand came from the library flop range: graded on that tree
    assert fb["street"] == "flop" and fb["origin"] == "solve" and fb["grade"] in ("best", "mixed", "blunder")
    assert t.get(v["sid"]).hand.drill_landed


def test_solve_note_only_for_a_missing_heads_up_flop(real_freqs: dict, tmp_path: Path) -> None:
    t = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False, freqs=real_freqs)
    v = t.new(Config(seed=11, drill_share=0.0, hero_seat="BB"))
    seen = {"preflop": 0, "no_library": 0}
    for _ in range(120):
        while not v["hand_over"]:
            toks = [a["token"] for a in v["legal_actions"]]
            v = t.act(v["sid"], "fold" if "fold" in toks and v["hand_no"] % 3 == 0
                      else "call" if "call" in toks else "check")  # fmt: skip
        hu_flop = v["street"] != "preflop" and sum(s["in_hand"] for s in v["seats"]) == 2
        if v["street"] == "preflop":  # walks, folds around, hero folds: no flop was needed
            seen["preflop"] += 1
            assert v["solve"] == {"status": "none", "backend": None, "note": None, "name": None,
                                  "detail": None}
        elif v["solve"]["note"] == "no_library_solve":
            seen["no_library"] += 1
            assert hu_flop and v["street"] == "flop"
        v = t.next(v["sid"])
    assert seen["preflop"] and seen["no_library"]


def test_flop_ranges_are_each_players_first_reach() -> None:
    doc = {"root": "r", "combos": ["AsAh", "KsKh", "7c2d"],
           "players": [{"position": "BB"}, {"position": "BTN"}],
           "nodes": {"r": {"type": "action", "player": 0, "reach": [1.0, 0.0, 0.5],
                           "actions": [{"token": "check", "child": "c"}]},
                     "c": {"type": "action", "player": 1, "reach": [0.0, 1.0, 0.25],
                           "actions": [{"token": "check", "child": "t"}]},
                     "t": {"type": "chance"}}}  # fmt: skip
    assert flop_ranges(doc) == {"BB": {"AsAh": 1.0, "7c2d": 0.5}, "BTN": {"KsKh": 1.0, "7c2d": 0.25}}
