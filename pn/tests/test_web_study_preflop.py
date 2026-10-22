"""Preflop study navigator API (contracts/study_preflop.md): GET /api/study/preflop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from pn.solve import SolveCache
from pn.solve.request import TABLE_SEATS
from pn.train import flop_library as flib
from pn.train import preflop as pre
from pn.train.engine import apply, start
from pn.web.app import create_app

URL = "/api/study/preflop"
HU_LINE = "LJ:fold,HJ:raise,CO:fold,BTN:call,SB:fold,BB:fold"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    solves = tmp_path / "solves"
    solves.mkdir()
    c = TestClient(create_app(tmp_path / "none.sqlite", solves))
    c.app.state.solve_cache = SolveCache(tmp_path / "cache")
    return c


def get(client: TestClient, line: str, **params: int) -> dict:
    r = client.get(URL, params={"line": line, **params})
    assert r.status_code == 200, r.text
    return r.json()


def seat(d: dict, pos: str) -> dict:
    return next(s for s in d["seats"] if s["position"] == pos)


def test_root_is_lj_rfi(client: TestClient) -> None:
    d = get(client, "")
    assert d["actor"] == "LJ" and d["end"] is None and d["pot"] == 150
    assert [s["position"] for s in d["seats"]] == list(TABLE_SEATS[6])
    lj = seat(d, "LJ")
    assert lj["status"] == "to_act"
    assert [o["token"] for o in lj["options"]] == ["fold", "call", "raise", "allin"]
    assert all(o["freq"] is not None for o in lj["options"])
    assert seat(d, "HJ") == {**seat(d, "HJ"), "status": "waiting", "options": [], "chosen": None}
    assert len(d["cells"]) == 169
    assert d["situation"]["source"] == "pokercoaching" and d["situation"]["kind"] == "rfi"
    assert sum(d["summary"].values()) == pytest.approx(1.0, abs=1e-3)


def three_bet_to() -> int:
    """BTN's raise-to after LJ fold, HJ open, CO fold, by pn.train.preflop.raise_to."""
    st = start(TABLE_SEATS[6], 10000, 50, 100)
    for code in ("F", "R", "F", None):
        sit = pre.situation(st, st.to_act)
        found = pre.gtow_spot(sit.key, 100)
        size = pre.raise_to(st, st.to_act, sit, found[1] if found else None)
        if code is None:
            return size
        st = apply(st, *pre.code_action(st, st.to_act, code, size))
    raise AssertionError


def test_three_bet_line_reaches_vs_3bet(client: TestClient) -> None:
    d = get(client, "LJ:fold,HJ:raise,CO:fold,BTN:raise")
    assert d["actor"] == "SB"
    d = get(client, "LJ:fold,HJ:raise,CO:fold,BTN:raise,SB:fold,BB:fold")
    assert d["actor"] == "HJ" and d["situation"]["kind"] == "vs_3bet"
    btn = seat(d, "BTN")
    assert btn["chosen"] == "raise" and btn["status"] == "acted"
    assert btn["committed"] == three_bet_to()  # sized like the trainer's villains
    assert seat(d, "LJ")["status"] == "folded" and all(
        o["freq"] is None for o in seat(d, "LJ")["options"]
    )


def test_sb_limp_gives_bb_vs_limp(client: TestClient) -> None:
    d = get(client, "LJ:fold,HJ:fold,CO:fold,BTN:fold,SB:call")
    assert d["actor"] == "BB" and d["situation"]["kind"] == "vs_limp"
    assert d["situation"]["source"] in ("gtowizard", "chart")
    assert seat(d, "SB")["committed"] == 100
    assert next(o for o in seat(d, "SB")["options"] if o["token"] == "call")["label"] == "Limp"
    assert {o["token"] for o in seat(d, "BB")["options"]} >= {"check", "raise"}


@pytest.mark.parametrize("line,step", [("LJ:fold,LJ:raise", "step 2"),
                                       ("LJ:fold,HJ:raise,CO:check", "step 3"),
                                       ("LJ:bet", "step 1")])  # fmt: skip
def test_illegal_line_is_400(client: TestClient, line: str, step: str) -> None:
    r = client.get(URL, params={"line": line})
    assert r.status_code == 400 and step in r.json()["detail"]


def test_heads_up_end_lists_solved_boards(client: TestClient) -> None:
    steps = [tuple(s.split(":")) for s in HU_LINE.split(",")]
    st = flib.replay(TABLE_SEATS[6], 100, steps, sb=50, bb=100)
    m = flib.matchup_of(st, "6max", 100)
    key = "ab" * 32
    index = {
        "matchups": {m.mid: {"label": m.label, "boards": {"Kc9c8d": {"key": key, "weight": 3}}}}
    }
    root = client.app.state.solve_cache.root
    root.mkdir(parents=True, exist_ok=True)
    (root / flib.INDEX_NAME).write_text(json.dumps(index))
    end = get(client, HU_LINE)["end"]
    assert end["kind"] == "flop" and end["players"] == ["HJ", "BTN"] and end["solvable"]
    assert end["matchup"]["mid"] == m.mid and end["matchup"]["pot_type"] == "srp"
    assert end["boards"] == [{"board": "Kc9c8d", "solve": "cache-" + key[:16], "weight": 3}]
    assert end["note"] is None


def test_multiway_end_has_no_matchup(client: TestClient) -> None:
    d = get(client, "LJ:fold,HJ:raise,CO:call,BTN:call,SB:fold,BB:fold")
    assert d["actor"] is None and d["cells"] is None
    end = d["end"]
    assert "multiway" in end["note"] and end["matchup"] is None and end["boards"] == []
    assert not end["solvable"]


def test_everyone_folds_is_decided(client: TestClient) -> None:
    end = get(client, "LJ:fold,HJ:fold,CO:fold,BTN:fold,SB:fold")["end"]
    assert end["kind"] == "decided" and end["players"] == ["BB"] and not end["solvable"]
    r = client.get(URL, params={"line": "LJ:fold,HJ:fold,CO:fold,BTN:fold,SB:fold,BB:check"})
    assert r.status_code == 400 and "step 6" in r.json()["detail"]
