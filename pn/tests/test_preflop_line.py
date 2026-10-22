"""Preflop range split along the played line (pn.train.preflop_line, GET /api/train/{sid}/preflop_line)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pn import ranges
from pn.adapter.state import Line
from pn.solve.build import _depth_bb
from pn.solve.request import TABLE_SEATS
from pn.solve.sources import seat_range
from pn.train import flop_library as flib
from pn.train.engine import apply, next_street, start
from pn.train.preflop_line import preflop_line

BOARD = ("2c", "3d", "4h")
TOL = 0.05  # combos per class


def _flop(plan: list[tuple[str, str]], table: int = 6, depth: int = 100):
    st = flib.replay(TABLE_SEATS[table], depth, plan, sb=50, bb=100, fold_others=True)
    assert st is not None
    tokens = [a.kind for a in st.history]
    return next_street(st, BOARD), tokens


def _taken_slice(step: dict) -> dict[str, float]:
    k = step["actions"].index(step["taken"])
    return {cls: c["reach"] * c["freq"][k] for cls, c in step["classes"].items()}


def _flop_root(st, table_kind: str = "6max") -> dict[str, dict[str, float]]:
    """Per-class combos of each seat's flop root range (the library request's seat ranges, as
    pn.solve.build passes them: remaining depth, 3-bettor, lone limper)."""
    req = flib.library_request(st, table_kind)
    three = next((s.position for s in req.seats if s.line == "3bet"), None)
    limpers = [s.position for s in req.seats if s.line == "limp"]
    out = {}
    for seat in req.seats:
        rng, _ = seat_range(position=seat.position, line=Line(seat.line, seat.vs), table=table_kind,
                            depth_bb=_depth_bb(req), three_bettor=three,
                            limper=limpers[0] if len(limpers) == 1 else None)  # fmt: skip
        out[seat.position] = {cls: sum(rng.get(c, 0.0) for c in ranges.class_combos(cls))
                              for cls in ranges.all_classes()}  # fmt: skip
    return out


@pytest.mark.parametrize("plan", [
    [("BTN", "raise"), ("SB", "raise"), ("BTN", "call")],
    [("CO", "raise"), ("BB", "call")],
    [("HJ", "raise"), ("BB", "raise"), ("HJ", "call")],
])  # fmt: skip
def test_final_slices_match_flop_root_ranges(plan: list[tuple[str, str]]) -> None:
    st, tokens = _flop(plan)
    steps = preflop_line(st, tokens, None, "6max", 100)["steps"]
    assert [(s["position"], s["taken"]) for s in steps] == plan
    root = _flop_root(st)
    for pos, want in root.items():
        got = _taken_slice([s for s in steps if s["position"] == pos][-1])
        bad = {c: (round(got[c], 3), want[c]) for c in want if abs(got[c] - want[c]) > TOL}
        assert not bad, (pos, bad)


@pytest.mark.parametrize(("plan", "depth", "hero"), [
    ([("CO", "raise"), ("BTN", "raise"), ("CO", "raise"), ("BTN", "call")], 100, None),
    ([("BTN", "raise"), ("BB", "raise"), ("BTN", "raise"), ("BB", "call")], 200, None),
    ([("SB", "call"), ("BB", "check")], 100, "SB"),  # hero's graded SB limps (bots never limp)
])  # fmt: skip
def test_policy_seat_ranges_equal_the_final_slices(plan, depth: int, hero: str | None) -> None:
    """The seats whose flop range follows the policy (pn.solve.sources.policy_line_range: the
    call of the 4-bet, both limped SB-BB seats) match the panel's final slices per class."""
    st, tokens = _flop(plan, depth=depth)
    steps = preflop_line(st, tokens, st.seat_of(hero) if hero else None, "6max", depth)["steps"]
    root = _flop_root(st)
    seats = {plan[-1][0], plan[0][0]} if hero else {plan[-1][0]}  # the 4-bettor already matched
    for pos in seats:
        got = _taken_slice([s for s in steps if s["position"] == pos][-1])
        bad = {c: (round(got[c], 4), w) for c, w in root[pos].items() if abs(got[c] - w) > 1e-3}
        assert not bad, (pos, bad)


def test_step_shape_and_reach_propagation() -> None:
    st, tokens = _flop([("BTN", "raise"), ("SB", "raise"), ("BTN", "call")])
    open_, three, call = preflop_line(st, tokens, None, "6max", 100)["steps"]
    assert open_["actions"] == ["fold", "call", "raise"] and open_["taken"] == "raise"
    for step in (open_, three, call):
        assert set(step["classes"]) == set(ranges.all_classes()) and len(step["classes"]) == 169
        for c in step["classes"].values():
            assert len(c["freq"]) == len(step["actions"])
            assert abs(sum(c["freq"]) - 1) < 1e-3 if c["reach"] else sum(c["freq"]) == 0
    assert open_["classes"]["AA"] == {"reach": 6.0, "n": 6, "freq": [0.0, 0.0, 1.0]}
    assert {c["n"] for c in open_["classes"].values()} == {4, 6, 12}
    assert all(c["reach"] == c["n"] for c in open_["classes"].values())  # nobody acted before
    assert all(c["reach"] == c["n"] for c in three["classes"].values())  # SB's first decision
    k = open_["actions"].index("raise")
    for cls, c in call["classes"].items():  # BTN enters vs the 3-bet with its opening range
        before = open_["classes"][cls]
        assert c["reach"] == pytest.approx(before["reach"] * before["freq"][k], abs=1e-3)
    assert call["classes"]["72o"]["reach"] == 0 and call["classes"]["72o"]["freq"] == [0, 0, 0]


def test_no_steps_unless_heads_up_flop() -> None:
    pre = start(TABLE_SEATS[6], 10000, 50, 100)
    pre = apply(pre, "raise", 250)
    assert preflop_line(pre, ["raise"], None, "6max", 100) == {"steps": []}  # still preflop
    three_way = pre  # HJ, CO fold, BTN calls, SB folds, BB calls
    for kind in ("fold", "fold", "call", "fold", "call"):
        three_way = apply(three_way, kind)
    three_way = next_street(three_way, BOARD)
    tokens = [a.kind for a in three_way.history]
    assert preflop_line(three_way, tokens, None, "6max", 100) == {"steps": []}


def test_hero_steps_use_the_graded_distribution() -> None:
    """Bots never limp the SB (sb_limp_as_raise); hero's SB step keeps the chart limps."""
    st, tokens = _flop([("SB", "call"), ("BB", "check")])
    sb = st.seat_of("SB")
    bot = preflop_line(st, tokens, None, "6max", 100)["steps"][0]
    hero = preflop_line(st, tokens, sb, "6max", 100)["steps"][0]
    assert sum(_taken_slice(bot).values()) == 0
    assert sum(_taken_slice(hero).values()) > 0


# ------------------------------------------------------------------ route
def test_preflop_line_route(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from pn.solve import SolveCache
    from pn.train import Trainer
    from pn.web.app import create_app

    client = TestClient(create_app(tmp_path / "none.sqlite", tmp_path))
    t = Trainer(SolveCache(tmp_path / "cache"), background=False)
    client.app.state.trainer = t
    v = client.post("/api/train/new", json={"seed": 1, "hero_seat": "BTN", "rng_enabled": False,
                                            "drill_share": 0}).json()  # fmt: skip
    sid = v["sid"]
    if v["street"] == "preflop":  # hero to act preflop: no flop yet
        assert client.get(f"/api/train/{sid}/preflop_line").json() == {"steps": []}
    st, tokens = _flop([("BTN", "raise"), ("SB", "raise"), ("BTN", "call")])
    s = t.get(sid)
    s.hand = replace(s.hand, state=st, tokens=tuple(tokens), origins=(None,) * len(tokens))
    out = client.get(f"/api/train/{sid}/preflop_line").json()
    assert [(x["position"], x["taken"]) for x in out["steps"]] == [("BTN", "raise"), ("SB", "raise"),
                                                                   ("BTN", "call")]  # fmt: skip
    assert out["steps"][0]["classes"]["AKs"]["n"] == 4
    assert client.get("/api/train/nope/preflop_line").status_code == 404
