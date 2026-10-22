"""Plan 7 study UI routes (pn/web/solve_routes.py): spot builder body, job progress + cancel,
saved-solves library, delete, ranges at a node, preflop line catalog, presets. Fake runner:
fixtures/fake_tree.fake_doc behind a SolveCache that accepts the progress / should_stop hooks."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fake_tree import fake_doc
from fastapi.testclient import TestClient

from pn import ranges
from pn.adapter.validate import to_toml
from pn.solve import SolveCache
from pn.solve.backend import Preempted
from pn.web.app import create_app

PROGRESS_STEPS = 3


class HookedCache(SolveCache):
    """SolveCache with the Plan 7 hook signature (stream T1 adds it to the real one)."""

    def __init__(self, root: Path, gate: threading.Event | None = None) -> None:
        super().__init__(root, runner=self._run)
        self.gate = gate
        self.calls: list[dict] = []
        self._hooks: dict = {}

    def solve(self, spot: Mapping[str, Any], backend: str | None = None, *, priority: bool = False,
              background: bool = False, progress=None, should_stop=None) -> dict:  # fmt: skip
        self._hooks = {"progress": progress, "should_stop": should_stop}
        self.calls.append({"spot": spot, "hooks": dict(self._hooks)})
        return super().solve(spot, backend, priority=priority, background=background)

    def _run(self, spot: Mapping[str, Any], backend: str | None = None) -> tuple[dict, str]:
        progress, stop = self._hooks["progress"], self._hooks["should_stop"]
        for i in range(PROGRESS_STEPS):
            if progress:
                progress({"iterations": 10 * (i + 1), "exploitability_pct_pot": 8.0 / (i + 1),
                          "elapsed_sec": 0.1 * (i + 1)})  # fmt: skip
        if self.gate is not None:  # block until cancelled (should_stop) or released
            while not self.gate.is_set():
                if stop and stop():
                    raise Preempted("stopped")
                time.sleep(0.01)
        doc = fake_doc(spot)
        doc["meta"] = {"backend": "texas", "ev_available": False, "iterations": 30,
                       "exploitability_pct_pot": 2.5, "stop": "converged"}  # fmt: skip
        return doc, "texas"


@pytest.fixture
def app_client(tmp_path: Path) -> TestClient:
    solves = tmp_path / "solves"
    solves.mkdir()
    c = TestClient(create_app(tmp_path / "none.sqlite", solves))
    c.app.state.solve_cache = HookedCache(tmp_path / "cache")
    return c


def _wait(c: TestClient, jid: str, until: tuple[str, ...] = ("done", "error", "cancelled")) -> dict:
    for _ in range(400):
        job = c.get(f"/api/solves/jobs/{jid}").json()
        if job["status"] in until:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {jid} stuck: {job}")


TURN_BODY = {"street": "turn", "board": ["Kd", "7s", "2c", "9h"], "line": "srp", "preset": "trainer",
             "seats": [{"position": "BTN"}, {"position": "BB"}],
             "ranges": {"oop": "AA,KK,72s", "ip": "QQ:0.5,AKs"},
             "sizes": {"turn_bets": [75], "river_bets": [150], "allin_threshold": 0.8},
             "pot": 1200, "stack": 8000}  # fmt: skip


# ------------------------------------------------------------------ presets / lines
def test_presets_lists_budgets_and_default_sizes(app_client: TestClient) -> None:
    d = app_client.get("/api/solves/presets").json()
    names = {p["name"] for p in d["presets"]}
    assert {"trainer", "review", "library"} <= names
    for p in d["presets"]:
        assert p["max_iterations"] > 0 and p["accuracy_pct_pot"] > 0
    assert "turn_bets" in d["default_sizes"] and "allin_threshold" in d["default_sizes"]


def test_line_catalog_and_line_ranges(app_client: TestClient) -> None:
    lines = app_client.get("/api/solves/lines", params={"table": 6}).json()
    assert len(lines) == 2 * 15  # srp + 3bp for every pair of the 6 seats
    btn_bb = next(x for x in lines if x["id"] == "srp:BTN:BB")
    assert btn_bb["oop"] == "BB" and btn_bb["ip"] == "BTN" and btn_bb["label"] == "BTN opens, BB calls"
    assert app_client.get("/api/solves/lines", params={"table": 7}).status_code == 400
    r = app_client.get("/api/solves/line", params={"line": "srp", "positions": "BTN,BB"}).json()
    assert r["pot"] == 650 and r["stack"] == 9700
    assert r["oop"]["position"] == "BB" and r["ip"]["position"] == "BTN"
    assert r["oop"]["range"] and r["ip"]["combos"] > 100
    deep = app_client.get("/api/solves/line", params={"line": "3bp", "positions": "BTN,BB", "depth": 200}).json()
    assert deep["pot"] > r["pot"] and deep["stack"] > 15000
    bad = app_client.get("/api/solves/line", params={"line": "4bp", "positions": "BTN,BB"})
    assert bad.status_code == 422


# ------------------------------------------------------------------ new spot + progress
def test_new_turn_spot_with_ranges_sizes_and_progress(app_client: TestClient) -> None:
    r = app_client.post("/api/solves/new", json=TURN_BODY)
    assert r.status_code == 202, r.text
    job = _wait(app_client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["iterations"] == 10 * PROGRESS_STEPS
    assert job["exploitability_pct_pot"] == pytest.approx(8.0 / PROGRESS_STEPS, rel=1e-3)
    assert [p[1] for p in job["curve"]] == [10, 20, 30]
    spot = app_client.app.state.solve_cache.calls[-1]["spot"]
    assert spot["game"]["street"] == "turn" and spot["game"]["pot"] == 1200
    assert spot["game"]["board"] == ["Kd", "7s", "2c", "9h"]
    by_pos = {p["position"]: p for p in spot["players"]}
    bb_range, btn_range = (ranges.parse(by_pos[p]["range"]) for p in ("BB", "BTN"))
    assert sum(bb_range.values()) == 6 + 3 + 2  # AA, KK minus Kd, 72s minus 7s2s / 7c2c
    assert sum(btn_range.values()) == pytest.approx(6 * 0.5 + 3)  # QQ:0.5, AKs minus AdKd
    assert by_pos["BB"]["stack"] == by_pos["BTN"]["stack"] == 8000
    assert spot["sizes"]["allin_threshold"] == 0.8
    meta = app_client.get(f"/api/solves/{job['name']}").json()
    assert meta["spot"]["sizes"] == TURN_BODY["sizes"] and meta["spot"]["pot"] == 1200
    assert meta["board"] == TURN_BODY["board"]


@pytest.mark.parametrize(("change", "fragment"), [
    ({"seats": [{"position": "BTN"}, {"position": "BB"}, {"position": "CO"}]}, "heads-up"),
    ({"board": ["Kd", "Kd", "2c", "9h"]}, "repeated"),
    ({"board": ["Kx", "7s", "2c", "9h"]}, "bad"),
    ({"sizes": {"flop_bets": [33], "preflop": {}}}, "unknown sizes"),
    ({"sizes": {"turn_bets": []}}, "turn_bets"),
    ({"street": "river"}, "does not start the river"),
    ({"ranges": {"CO": "AA"}}, "not a seat"),
    ({"pot": -5}, "positive"),
])  # fmt: skip
def test_new_spot_is_validated_before_the_job(app_client: TestClient, change: dict, fragment: str) -> None:
    r = app_client.post("/api/solves/new", json={**TURN_BODY, **change})
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]


def test_hooks_are_passed_only_when_the_cache_accepts_them(app_client: TestClient, tmp_path: Path) -> None:
    from fake_tree import fake_runner

    app_client.app.state.solve_cache = SolveCache(tmp_path / "plain", runner=fake_runner)
    job = _wait(app_client, app_client.post("/api/solves/new", json=TURN_BODY).json()["job_id"])
    assert job["status"] == "done" and job["iterations"] is None and job["curve"] == []


# ------------------------------------------------------------------ cancel
def test_cancel_a_running_job_stops_the_solver(app_client: TestClient, tmp_path: Path) -> None:
    gate = threading.Event()
    cache = HookedCache(tmp_path / "gated", gate=gate)
    app_client.app.state.solve_cache = cache
    jid = app_client.post("/api/solves/new", json=TURN_BODY).json()["job_id"]
    _wait(app_client, jid, until=("running",))
    for _ in range(200):  # until the runner is inside its stop-polling loop
        if cache.calls:
            break
        time.sleep(0.01)
    r = app_client.post(f"/api/solves/jobs/{jid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    time.sleep(0.2)
    job = app_client.get(f"/api/solves/jobs/{jid}").json()
    assert job["status"] == "cancelled" and "name" not in job
    assert not list((tmp_path / "gated").glob("*.json"))  # nothing cached
    assert cache.calls[-1]["hooks"]["should_stop"]() is True
    assert app_client.post("/api/solves/jobs/nope/cancel").status_code == 404
    gate.set()
    done = _wait(app_client, app_client.post("/api/solves/new", json=TURN_BODY).json()["job_id"])
    assert done["status"] == "done"
    assert app_client.post(f"/api/solves/jobs/{done['job_id']}/cancel").status_code == 409


# ------------------------------------------------------------------ library / delete / ranges
def _cached_doc(root: Path) -> str:
    """A cache entry written outside the registry (as the library fill does); its key."""
    spot = {"game": {"street": "flop", "board": ["As", "7d", "2c"], "pot": 550, "sb": 50, "bb": 100},
            "players": [{"name": "bb", "position": "BB", "stack": 9750, "range": "AA,KK,QQ"},
                        {"name": "btn", "position": "BTN", "stack": 9750, "range": "AKs,AQs"}],
            "sizes": {}}  # fmt: skip
    key = "ab" * 32
    root.mkdir(parents=True, exist_ok=True)
    doc = fake_doc(spot)
    for node in doc["nodes"].values():  # fake_doc's reach_by_player ignores the strategy product
        node.pop("reach_by_player", None)
    doc["meta"] = {"backend": "texas", "iterations": 99, "exploitability_pct_pot": 0.31}
    (root / f"{key}.json").write_text(json.dumps(doc))
    (root / f"{key}.toml").write_text(to_toml(spot))
    return key


def test_library_lists_opens_and_deletes_cached_solves(app_client: TestClient, tmp_path: Path) -> None:
    key = _cached_doc(tmp_path / "cache")
    job = _wait(app_client, app_client.post("/api/solves/new", json=TURN_BODY).json()["job_id"])
    rows = app_client.get("/api/solves/library").json()
    assert len(rows) == 2
    lone = next(r for r in rows if r["key"] == key)
    assert lone["name"] == f"cache-{key[:16]}" and lone["board"] == ["As", "7d", "2c"]
    assert lone["pot_type"] == "srp" and lone["depth_bb"] == 100 and lone["positions"] == ["BB", "BTN"]
    assert lone["exploitability_pct_pot"] == 0.31 and lone["iterations"] == 99 and lone["backend"] == "texas"
    mine = next(r for r in rows if r["key"] != key)
    assert mine["name"] == job["name"] and mine["street"] == "turn" and mine["line"] == "srp"
    meta = app_client.get(f"/api/solves/{lone['name']}").json()
    assert meta["board"] == ["As", "7d", "2c"] and meta["meta"]["backend"] == "texas"
    d = app_client.delete(f"/api/solves/{lone['name']}")
    assert d.status_code == 200 and lone["name"] in d.json()["deleted"]
    assert not (tmp_path / "cache" / f"{key}.json").exists()
    assert app_client.get(f"/api/solves/{lone['name']}").status_code == 404
    d2 = app_client.delete(f"/api/solves/{job['name']}")
    assert d2.status_code == 200 and len(d2.json()["files"]) == 2
    assert app_client.get("/api/solves/library").json() == []
    assert app_client.get(f"/api/solves/{job['name']}").status_code == 404
    assert app_client.delete(f"/api/solves/{job['name']}").status_code == 404
    assert app_client.delete("/api/solves/sample").status_code == 400


def test_ranges_at_a_node_walk_each_players_strategy(app_client: TestClient, tmp_path: Path) -> None:
    key = _cached_doc(tmp_path / "cache")
    name = f"cache-{key[:16]}"
    root = app_client.get(f"/api/solves/{name}/ranges").json()
    assert root["type"] == "action" and root["player"] == 0
    oop, ip = (sum(p["reach"]) for p in root["players"])
    assert oop == pytest.approx(6 + 6 + 6)  # AA, KK, QQ (the hand-written spot keeps blocked combos)
    assert ip == pytest.approx(4 + 4)  # AKs, AQs
    after = app_client.get(f"/api/solves/{name}/ranges", params={"path": "check"}).json()
    assert after["player"] == 1
    # fake strategies weight actions 3**j (1/4 check, 3/4 bet): OOP keeps a quarter, IP all
    assert sum(after["players"][0]["reach"]) == pytest.approx(oop / 4, rel=1e-4)
    assert sum(after["players"][1]["reach"]) == pytest.approx(ip, rel=1e-4)
    assert app_client.get(f"/api/solves/{name}/ranges", params={"path": "raise999"}).status_code == 404
    assert app_client.get("/api/solves/nope/ranges").status_code == 404


def test_study_pages_serve_the_spa(app_client: TestClient) -> None:
    for path in ("/strategy", "/strategy/new", "/strategy/cache-abc?node=check"):
        r = app_client.get(path)
        assert r.status_code == 200 and 'id="app"' in r.text
