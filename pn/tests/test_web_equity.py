"""Range equity at a strategy node (pn/web/equity.py, GET /api/solves/{name}/equity): the numpy
evaluator against handeval, exact river / enumerated turn and flop equity against a brute-force
reference, card removal, null rules and the route on the built-in sample solve."""

from __future__ import annotations

import itertools
import random
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from pn import ranges
from pn.profile.handeval import evaluate
from pn.web import equity as eqmod
from pn.web.app import create_app
from pn.web.equity import hand_values, range_equity

FLOP_BUDGET_SEC = 2.0


def _dense(text: str) -> list[float]:
    return ranges.to_dense(ranges.parse(text))


def _idx(combo: str) -> int:
    return ranges.combo_index(ranges.card_to_int(combo[:2]), ranges.card_to_int(combo[2:]))


def _brute(board: list[str], me: list[float], opp: list[float], hero: int) -> float | None:
    """Hand-vs-range equity of combo `hero` by walking every runout and opponent combo."""
    b = [ranges.card_to_int(c) for c in board]
    combos = ranges.all_combos()
    h = [ranges.card_to_int(combos[hero][:2]), ranges.card_to_int(combos[hero][2:])]
    if set(h) & set(b):
        return None
    live = [c for c in range(52) if c not in b and c not in h]
    num = den = 0.0
    for ext in itertools.combinations(live, 5 - len(b)):
        full = [*b, *ext]
        hv = evaluate([*h, *full])
        for j, w in enumerate(opp):
            o = [ranges.card_to_int(combos[j][:2]), ranges.card_to_int(combos[j][2:])]
            if not w or set(o) & {*h, *full}:
                continue
            ov = evaluate([*o, *full])
            num += w * (1.0 if hv > ov else 0.5 if hv == ov else 0.0)
            den += w
    return num / den if den else None


# ------------------------------------------------------------------ evaluator
def test_hand_values_match_handeval_and_mark_blocked_combos() -> None:
    rng = random.Random(7)
    boards = np.array([rng.sample(range(52), 5) for _ in range(30)], dtype=np.int64)
    vals = hand_values(boards)
    combos = ranges.all_combos()
    for r, board in enumerate(boards.tolist()):
        for i in rng.sample(range(ranges.N_COMBOS), 150):
            a, b = ranges.card_to_int(combos[i][:2]), ranges.card_to_int(combos[i][2:])
            want = -1 if {a, b} & set(board) else evaluate([a, b, *board])
            assert vals[r, i] == want


def test_hand_values_flush_and_straight_flush_boards() -> None:
    board = [ranges.card_to_int(c) for c in ("Ah", "Kh", "7h", "2h", "9c")]
    vals = hand_values(np.array([board]))[0]
    for combo in ("QhJh", "3h4c", "AsAd", "Th9h", "QhTh"):
        a, b = ranges.card_to_int(combo[:2]), ranges.card_to_int(combo[2:])
        assert vals[_idx(combo)] == evaluate([a, b, *board])


# ------------------------------------------------------------------ equity
def test_river_aa_vs_kk_is_exact_with_symmetric_totals() -> None:
    board = ["2c", "7d", "9h", "Js", "3c"]
    out = range_equity(board, [_dense("AA"), _dense("KK")], ["BB", "BTN"])
    assert out["street"] == "river" and out["method"] == "exact" and out["runouts"] == 1
    assert out["board"] == board
    bb, btn = out["players"]
    assert bb["position"] == "BB" and bb["equity"] == 1.0 and btn["equity"] == 0.0
    assert bb["combos"] == 6 and btn["combos"] == 6
    assert bb["per_combo"][_idx("AsAh")] == 1.0 and btn["per_combo"][_idx("KsKh")] == 0.0
    assert bb["per_combo"][_idx("QsQh")] is None  # zero reach -> null


def test_board_blocked_combos_are_null_and_not_counted() -> None:
    board = ["As", "Kd", "7h", "2c", "3s"]  # one ace and one king on board
    out = range_equity(board, [_dense("AA"), _dense("KK")], ["BB", "BTN"])
    bb, btn = out["players"]
    assert bb["combos"] == 3 and btn["combos"] == 3
    assert bb["per_combo"][_idx("AsAh")] is None and btn["per_combo"][_idx("KdKs")] is None
    assert bb["per_combo"][_idx("AhAd")] == 1.0  # set of aces beats the set of kings


def test_ties_split_and_matched_equities_sum_to_one() -> None:
    board = ["As", "Ks", "Qs", "Js", "Ts"]  # royal flush on board: everything chops
    out = range_equity(board, [_dense("22+"), _dense("AKo,72o")], ["SB", "BB"])
    assert [p["equity"] for p in out["players"]] == [0.5, 0.5]
    out = range_equity(["Ks", "7d", "2c", "9h"], [_dense("AA,KQs,T9s:0.5,76o"), _dense("KK,AQs,JTs,88:0.3")],
                       ["BB", "BTN"])  # fmt: skip
    a, b = (p["equity"] for p in out["players"])
    assert a + b == pytest.approx(1.0, abs=2e-4)


BRUTE_BOARDS = [["Ks", "7d", "2c", "9h", "As"], ["Ks", "7d", "2c", "9s"], ["Qh", "8d", "5h"]]


@pytest.mark.parametrize("board", BRUTE_BOARDS)
def test_per_combo_matches_brute_force(board: list[str]) -> None:
    me, opp = _dense("AA,KQs,T9s:0.5,76o"), _dense("KK,AQs,JTs,88:0.3")
    if len(board) == 3:  # flop brute force is slow: a few hero combos vs a small range
        opp = _dense("KK,JTs")
    out = range_equity(board, [me, opp], ["BB", "BTN"])
    assert out["runouts"] == {5: 1, 4: 48, 3: 1176}[len(board)]
    assert out["method"] == ("exact" if len(board) == 5 else "enumerated")
    pc = out["players"][0]["per_combo"]
    picks = [i for i, w in enumerate(me) if w > 0][:: 6 if len(board) == 3 else 1]
    for i in picks:
        want = _brute(board, me, opp, i)
        if want is None:
            assert pc[i] is None
        else:
            assert pc[i] == pytest.approx(want, abs=1e-4)


def test_unknown_reach_and_bad_boards() -> None:
    out = range_equity(["Ks", "7d", "2c"], [None, _dense("AA")], ["BB", "BTN"])
    bb, btn = out["players"]
    assert bb["equity"] is None and bb["combos"] == 0.0 and set(bb["per_combo"]) == {None}
    assert btn["equity"] is None and set(btn["per_combo"]) == {None}  # no opponent range
    with pytest.raises(ValueError):
        range_equity([], [_dense("AA"), _dense("KK")], ["BB", "BTN"])
    with pytest.raises(ValueError):
        range_equity(["Ks", "Ks", "2c"], [_dense("AA"), _dense("KK")], ["BB", "BTN"])
    with pytest.raises(ValueError):
        range_equity(["Ks", "7d", "2c"], [_dense("AA")] * 3, ["a", "b", "c"])


def test_flop_within_budget_and_cached() -> None:
    eqmod._results.clear()
    eqmod._plans.clear()
    reach = [_dense("22+,A2s+,K9s+,ATo+"), _dense("55+,A9s+,KTs+,AJo+")]
    t = time.perf_counter()
    first = range_equity(["Jc", "8d", "3h"], reach, ["BB", "BTN"])
    cold = time.perf_counter() - t
    t = time.perf_counter()
    again = range_equity(["Jc", "8d", "3h"], reach, ["BB", "BTN"])
    assert again is first and time.perf_counter() - t < 0.05
    assert cold < FLOP_BUDGET_SEC
    assert sum(p["equity"] for p in first["players"]) == pytest.approx(1.0, abs=2e-4)


def test_concurrent_cold_requests_build_each_board_once(monkeypatch) -> None:
    import threading

    eqmod._results.clear()
    eqmod._plans.clear()
    built: list[tuple[int, ...]] = []
    real = eqmod._Plan

    def counting(board):
        built.append(board)
        return real(board)

    monkeypatch.setattr(eqmod, "_Plan", counting)
    rng = random.Random(1)
    reaches = [[[rng.random() for _ in range(ranges.N_COMBOS)] for _ in range(2)] for _ in range(4)]
    out: list[dict] = []
    threads = [threading.Thread(target=lambda r=r: out.append(range_equity(["Ks", "7d", "2c", "9h"], r, ["a", "b"])))
               for r in reaches]  # fmt: skip
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(out) == 4 and built == [tuple(ranges.card_to_int(c) for c in ["Ks", "7d", "2c", "9h"])]


# ------------------------------------------------------------------ route
@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    solves = tmp_path / "solves"
    solves.mkdir()
    return TestClient(create_app(tmp_path / "none.sqlite", solves))


def _river_doc(board: list[str]) -> dict:
    """A one-decision river spot: BB holds AA, BTN holds KK."""
    reach = [_dense("AA"), _dense("KK")]
    return {
        "meta": {"board": board, "bb": 100, "iterations": 0},
        "players": [{"index": 0, "name": "bb", "position": "BB"}, {"index": 1, "name": "btn", "position": "BTN"}],
        "combos": list(ranges.all_combos()),
        "root": "n0",
        "nodes": {
            "n0": {"type": "action", "street": "river", "player": 0, "pot": 1000, "stacks": [9000, 9000],
                   "history": [], "actions": [{"token": "check", "child": "n1"}],
                   "strategy": [[1.0]] * ranges.N_COMBOS, "reach_by_player": reach},
            "n1": {"type": "terminal", "kind": "showdown", "pot": 1000, "reach_by_player": reach},
        },
    }  # fmt: skip


def test_route_river_spot_and_404s(client: TestClient) -> None:
    client.app.state.registry.register("tiny", _river_doc(["2c", "7d", "9h", "Js", "3c"]), "test")
    d = client.get("/api/solves/tiny/equity").json()
    assert d["street"] == "river" and d["method"] == "exact"
    assert [p["position"] for p in d["players"]] == ["BB", "BTN"]
    assert [p["equity"] for p in d["players"]] == [1.0, 0.0]
    assert len(d["players"][0]["per_combo"]) == ranges.N_COMBOS
    assert client.get("/api/solves/tiny/equity", params={"path": "check"}).status_code == 200
    assert client.get("/api/solves/tiny/equity", params={"path": "bet999"}).status_code == 404
    assert client.get("/api/solves/nope/equity").status_code == 404


def test_route_sample_reach_matches_ranges_and_chance_board(client: TestClient) -> None:
    paths = (("", "flop"), ("check", "flop"), ("check,check", "flop"), ("check,check,As", "turn"))
    for path, street in paths:
        d = client.get("/api/solves/sample/equity", params={"path": path}).json()
        rng = client.get("/api/solves/sample/ranges", params={"path": path}).json()
        assert d["street"] == street
        for p, r in zip(d["players"], rng["players"], strict=True):
            dead = set(d["board"])
            want = sum(w for c, w in zip(ranges.all_combos(), r["reach"], strict=True)
                       if c[:2] not in dead and c[2:] not in dead)  # fmt: skip
            assert p["combos"] == pytest.approx(want, abs=1e-3)
            assert p["position"] == r["position"]
            for x, w in zip(p["per_combo"], r["reach"], strict=True):
                assert (x is None) or (w > 0 and 0.0 <= x <= 1.0)
        a, b = (p["equity"] for p in d["players"])
        assert a + b == pytest.approx(1.0, abs=2e-4)
    turn = client.get("/api/solves/sample/equity", params={"path": "check,check,As"}).json()
    assert turn["board"] == ["Ks", "7d", "2c", "As"] and turn["runouts"] == 48


def test_route_node_param_matches_path(client: TestClient) -> None:
    """`node=<id>` (trainer feedback grid_ref) resolves to the same node as its path."""
    path = "check,check,As"
    by_path = client.get("/api/solves/sample/ranges", params={"path": path}).json()
    nid = by_path["id"]
    by_node = client.get("/api/solves/sample/ranges", params={"node": nid}).json()
    assert by_node == by_path
    eq_path = client.get("/api/solves/sample/equity", params={"path": path}).json()
    eq_node = client.get("/api/solves/sample/equity", params={"node": nid}).json()
    assert eq_node == eq_path
    assert client.get("/api/solves/sample/ranges", params={"node": "nope"}).status_code == 404
    assert client.get("/api/solves/sample/equity", params={"node": "nope"}).status_code == 404
