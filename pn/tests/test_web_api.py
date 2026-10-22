"""Web UI: every /api/* route and every SPA page route, against a DB built from the 298-hand log."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from pn.ingest import db
from pn.web.app import PAGES, STATIC, create_app
from pn.web.sample import generate_sample

GAME = "pglSAMPLE0000000000000000"
LOG = Path(__file__).resolve().parents[2] / "data" / "fixtures" / f"pokernow_{GAME}.json"
HERO = "hero000001"


@pytest.fixture(scope="module")
def sample() -> dict:
    return generate_sample()


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory, sample: dict) -> TestClient:
    if not LOG.exists():
        pytest.skip("real fixture log missing")
    from pn.ingest.parse import parse_messages, sort_messages

    root = tmp_path_factory.mktemp("web")
    hands = parse_messages(sort_messages(json.loads(LOG.read_text())), game_id=GAME)
    db.load(hands, root / "pn.sqlite")
    solves = root / "solves"
    solves.mkdir()
    (solves / "flop_test.json").write_text(json.dumps(sample))
    (solves / "broken.json").write_text("{not json")
    return TestClient(create_app(root / "pn.sqlite", solves))


# ------------------------------------------------------------------ players / profile / review
def test_players(client: TestClient) -> None:
    rows = client.get("/api/players").json()
    hero = next(r for r in rows if r["canonical_id"] == HERO)
    assert hero["hands"] == 136 and hero["name"]
    assert set(hero["stats"]) == {"vpip", "pfr", "three_bet", "af_flop", "wtsd"}
    assert [r["hands"] for r in rows] == sorted((r["hands"] for r in rows), reverse=True)
    assert sum(r["net"] for r in rows) == 0  # chips are conserved


def test_profile_is_positional(client: TestClient) -> None:
    p = client.get(f"/api/players/{HERO}/profile").json()
    assert p["canonical_id"] == HERO and p["hands"] == 136
    assert {"BTN", "BB", "IP", "OOP"} <= set(p["by_position"])
    assert "by_position_vs_opener" in p and "preflop_vs_gto" in p
    assert all(lk["spot"] != "all" for lk in p["leaks"])
    by_name = client.get(f"/api/players/{p['display_name']}/profile").json()
    assert by_name["canonical_id"] == HERO
    assert client.get("/api/players/nobody-here/profile").status_code == 404


def test_review(client: TestClient) -> None:
    r = client.get(f"/api/review/{HERO}").json()
    assert r["canonical_id"] == HERO and r["rows"]
    row = r["rows"][0]
    assert {"stat", "spot", "verdict", "reason", "detail", "cost"} <= set(row)
    assert row["verdict"] in ("justified", "leak", "unknown")
    assert client.get("/api/review/nobody-here").status_code == 404


# ------------------------------------------------------------------ hands
def test_hands_list_and_filters(client: TestClient) -> None:
    page = client.get("/api/hands", params={"limit": 10}).json()
    assert page["total"] == 298 and len(page["items"]) == 10 and page["player"] is None
    mine = client.get("/api/hands", params={"player": HERO, "limit": 500}).json()
    assert mine["total"] == 136 and all(i["hero"] is not None for i in mine["items"])
    river = client.get("/api/hands", params={"street": "river", "limit": 500}).json()
    assert river["total"] > 0 and all(i["street"] == "river" for i in river["items"])
    sd = client.get("/api/hands", params={"showdown": True, "limit": 500}).json()
    no_sd = client.get("/api/hands", params={"showdown": False, "limit": 1}).json()
    assert all(i["showdown"] for i in sd["items"]) and sd["total"] + no_sd["total"] == 298
    second = client.get("/api/hands", params={"limit": 10, "offset": 10}).json()
    assert {i["hand_id"] for i in second["items"]}.isdisjoint(i["hand_id"] for i in page["items"])
    assert client.get("/api/hands", params={"street": "fifth"}).status_code == 400
    assert client.get("/api/hands", params={"limit": 501}).status_code == 422
    assert client.get("/api/hands", params={"player": "nobody-here"}).status_code == 404


def test_hand_replay_conserves_chips(client: TestClient) -> None:
    hid = client.get("/api/hands", params={"street": "river", "limit": 1}).json()["items"][0][
        "hand_id"
    ]
    d = client.get(f"/api/hands/{hid}").json()
    hand = d["hand"]
    assert set(d["names"]) == {p["player_id"] for p in hand["players"]}
    assert [s["street"] for s in d["replay"]] == ["preflop", "flop", "turn", "river"]
    last = d["replay"][-1]
    final_pot = last["actions"][-1]["pot_after"] if last["actions"] else last["pot_start"]
    assert final_pot == sum(a["amount"] for a in hand["actions"])
    assert client.get("/api/hands/no-such-hand").status_code == 404


# ------------------------------------------------------------------ ranges / charts / GTO Wizard
def test_range_grid(client: TestClient) -> None:
    g = client.get("/api/range/grid", params={"text": "AA,AKs"}).json()
    assert g["grid"] == {"AA": 1.0, "AKs": 1.0} and g["width"] > 0
    assert client.get("/api/range/grid").json()["grid"] == {}
    assert client.get("/api/range/grid", params={"text": "ZZ9"}).status_code == 400


def test_charts(client: TestClient) -> None:
    files = client.get("/api/charts").json()["files"]
    assert files, "no chart files in config/preflop_charts"
    name, keys = next((n, k) for n, k in files.items() if k)
    g = client.get("/api/charts/grid", params={"file": name, "key": keys[0]}).json()
    assert g["file"] == name and g["key"] == keys[0] and g["grid"]
    assert client.get("/api/charts/grid", params={"file": name, "key": "nope"}).status_code == 404
    assert client.get("/api/charts/grid", params={"file": "x.txt", "key": "a"}).status_code == 404


def test_pokercoaching_chart_spot(client: TestClient) -> None:
    params = {"table": "6max", "situation": "vs_open", "position": "BB", "other": "BTN"}
    d = client.get("/api/charts/pc", params=params).json()
    assert d["key"] == "3bet BB vs BTN" and d["source"] == "pokercoaching" and len(d["cells"]) == 169
    assert all(len(sh) == 1 for sh in d["cells"].values())  # pure chart: one action per class
    assert d["cells"]["AA"] == {"R": 1.0} and d["cells"]["72o"] == {"F": 1.0}
    assert d["labels"]["R"] == "3-bet" and sum(d["summary"].values()) == pytest.approx(1.0, abs=1e-3)
    params = {"table": "6max", "situation": "vs_4bet", "position": "BTN", "other": "SB"}
    assert client.get("/api/charts/pc", params=params).status_code == 404


def test_gtow_spots(client: TestClient) -> None:
    spots = client.get("/api/gtow/spots").json()
    if not spots:
        pytest.skip("GTO Wizard freqs missing")
    key = next(s["key"] for s in spots if s["kind"] == "vs_open")
    d = client.get("/api/gtow/spot", params={"key": key}).json()
    assert d["kind"] == "vs_open" and len(d["cells"]) == 169
    assert sum(d["summary"].values()) == pytest.approx(1.0, abs=1e-3)
    assert client.get("/api/gtow/spot", params={"key": "100|rfi|XX"}).status_code == 404


# ------------------------------------------------------------------ solves
def test_solves_list_meta_node_walk(client: TestClient, sample: dict) -> None:
    names = {r["name"] for r in client.get("/api/solves").json()}
    assert {"sample", "flop_test", "broken"} <= names
    for name in ("sample", "flop_test"):
        meta = client.get(f"/api/solves/{name}").json()
        assert meta["board"] == ["Ks", "7d", "2c"] and len(meta["combos"]) == 1326
        root = client.get(f"/api/solves/{name}/nodes/{meta['root']}").json()
        assert root["type"] == "action" and len(root["strategy"]) == 1326
    turn = client.get("/api/solves/flop_test/walk", params={"path": "check,check,As"}).json()
    assert turn["street"] == "turn" and turn["type"] == "action"
    assert client.get("/api/solves/flop_test/walk", params={"path": "allin"}).status_code == 404
    assert client.get("/api/solves/flop_test/nodes/zzz").status_code == 404
    assert client.get("/api/solves/missing").status_code == 404
    assert client.get("/api/solves/broken").status_code == 422


def test_solve_upload(client: TestClient, sample: dict) -> None:
    raw = json.dumps(sample).encode()
    r = client.post("/api/solves/upload", files={"file": ("mine.json", raw, "application/json")})
    name = r.json()["name"]
    assert r.status_code == 200 and name.startswith("upload-mine-")
    assert name in {s["name"] for s in client.get("/api/solves").json()}
    assert client.get(f"/api/solves/{name}").json()["node_count"] == len(sample["nodes"])
    bad = client.post("/api/solves/upload", files={"file": ("x.json", b'{"a": 1}', "text/json")})
    assert bad.status_code == 422


# ------------------------------------------------------------------ pages + static SPA
def test_pages_serve_the_spa(client: TestClient) -> None:
    for route in PAGES:
        path = re.sub(r"\{\w+\}", "x1", route)
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith("text/html") and 'id="app"' in r.text


def test_static_assets_and_imports_resolve(client: TestClient) -> None:
    html = client.get("/").text
    refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert "/static/js/main.js" in refs and "/static/app.css" in refs
    for ref in refs:
        assert client.get(ref).status_code == 200, ref
    for js in STATIC.rglob("*.js"):
        text = js.read_text()
        for spec in re.findall(r'from\s+"(\.{1,2}/[^"]+)"', text):
            target = (js.parent / spec).resolve()
            assert target.exists(), f"{js.name} imports missing {spec}"
            url = "/static/" + target.relative_to(STATIC).as_posix()
            assert client.get(url).status_code == 200, url


def test_missing_db_is_503(tmp_path: Path) -> None:
    c = TestClient(create_app(tmp_path / "none.sqlite", tmp_path))
    assert c.get("/api/players").status_code == 503
    assert c.get("/").status_code == 200  # the SPA shell still loads


# ------------------------------------------------------------------ trainer
FAKE_SOLVER = Path(__file__).parent / "fixtures" / "fake_solver.py"


def test_train_session_routes(client: TestClient, tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(FAKE_SOLVER.parent))
    from fake_tree import fake_runner

    from pn.solve import SolveCache
    from pn.train import Trainer

    client.app.state.trainer = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False)
    players = client.get("/api/train/players").json()
    assert players and {"player_id", "name", "hands"} <= set(players[0])
    v = client.post("/api/train/new", json={"seed": 4, "hero_seat": "CO",
                                            "opponents": [HERO]}).json()  # fmt: skip
    assert v["hero"]["position"] == "CO" and len(v["seats"]) == 6 and v["table"] == 6
    assert v["opponents"][0] == HERO and v["seats"][0]["is_hero"]
    sid = v["sid"]
    assert client.get(f"/api/train/{sid}").json()["hand_no"] == 1
    assert client.post(f"/api/train/{sid}/act", json={"token": "nope"}).status_code == 400
    assert client.post(f"/api/train/{sid}/next").status_code == 400  # hand still running
    for _ in range(40):
        if v["hand_over"]:
            break
        toks = [o["token"] for o in v["legal_actions"]]
        tok = next(t for t in ("check", "call", "fold") if t in toks)
        v = client.post(f"/api/train/{sid}/act", json={"token": tok}).json()
        assert v["feedback"]["chosen"] == tok and v["feedback"]["options"]
    assert v["hand_over"] and v["result"]["winner"] and v["totals"]["hands"] == 1
    assert client.post(f"/api/train/{sid}/next").json()["hand_no"] == 2
    assert client.get("/api/train/nope").status_code == 404
    assert client.post("/api/train/new", json={"table": 7}).status_code == 400


def test_train_rng_options_history_and_stats(client: TestClient, tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(FAKE_SOLVER.parent))
    from fake_tree import fake_runner

    from pn.solve import SolveCache
    from pn.train import Trainer

    db = client.app.state.store.db_path
    client.app.state.trainer = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False,
                                       db=db)  # fmt: skip
    before = client.get("/api/train/stats", params={"window": 50}).json()
    v = client.post("/api/train/new", json={"seed": 4, "hero_seat": "CO", "rng_enabled": False,
                                            "window": 50}).json()  # fmt: skip
    assert v["rng_enabled"] is False and v["rng"] is None
    assert v["rolling"]["window"] == 50 and v["rolling"]["hands"] == before["hands"]
    sid = v["sid"]
    for _ in range(40):
        if v["hand_over"]:
            break
        toks = [o["token"] for o in v["legal_actions"]]
        v = client.post(f"/api/train/{sid}/act",
                        json={"token": next(t for t in ("check", "call", "fold") if t in toks)}).json()  # fmt: skip
        fb = v["feedback"]
        assert fb["rng"] is None and fb["matched_rng"] is None and "buckets" in fb and "pure" in fb
    assert v["hand_over"] and v["rolling"]["hands"] == before["hands"] + 1
    s = client.get("/api/train/stats", params={"window": 50, "source": "gto"}).json()
    assert s["hands"] == before["hands"] + 1 and s["window"] == 50 and s["decisions"] >= 1
    assert {"by_street", "by_position", "by_situation", "slices", "rng_match_rate"} <= set(s)
    assert s["by_position"]["CO"]["hands"] >= 1
    assert client.get("/api/train/stats", params={"source": "custom"}).json()["hands"] == 0
    assert client.get("/api/train/stats", params={"window": 0}).status_code == 400
    assert client.post("/api/train/new", json={"window": 0}).status_code == 400


def test_train_leaks_and_drills(client: TestClient, tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(FAKE_SOLVER.parent))
    import fake_preflop
    from fake_tree import fake_runner

    from pn.solve import SolveCache
    from pn.solve.lazydoc import LazyDoc
    from pn.train import Trainer
    from pn.train.leaks import Leaks

    lib = LazyDoc.open(fake_preflop.write(tmp_path / "lib.json"))
    db = client.app.state.store.db_path
    client.app.state.trainer = Trainer(
        SolveCache(tmp_path / "cache", runner=fake_runner), background=False, db=db,
        leaks=Leaks(db, library=lambda table, depth: lib))  # fmt: skip
    rows = client.get("/api/train/leaks", params={"window": 200}).json()
    assert rows and set(rows[0]) == {"spot", "label", "freq", "n", "blunders", "blunder_rate",
                                     "expected_blunders_per_100", "weight", "priority", "score",
                                     "pot_bb", "formation"}  # fmt: skip
    assert sum(r["weight"] for r in rows) == pytest.approx(1.0, abs=1e-3)
    prio = [r["priority"] for r in rows]
    assert prio == sorted(prio, reverse=True) and prio[0] == 1.0
    p5 = client.get("/api/train/leaks", params={"window": 200, "p_form": 5, "p_tex": 2, "pot": "avg"})
    assert p5.status_code == 200 and p5.json()[0]["priority"] == 1.0
    for bad in ({"p_form": 0}, {"p_form": 6}, {"p_tex": 9}, {"pot": "mean"}):
        assert client.get("/api/train/leaks", params=bad).status_code == 400, bad
    assert client.get("/api/train/leaks", params={"window": 0}).status_code == 400
    assert client.get("/api/train/leaks", params={"table": 7}).status_code == 400
    v = client.post("/api/train/new", json={"seed": 3, "drill_share": 1.0}).json()
    assert v["drill"]["spot"] and v["drill"]["label"] and v["solve_error"] is None
    assert v["hero"]["position"] == client.app.state.trainer.get(v["sid"]).hand.drill.line.hero
    assert len(v["rolling"]["leaks_top"]) == 3
    natural = client.post("/api/train/new", json={"seed": 3, "drill_share": 0}).json()
    assert natural["drill"] is None
    assert client.post("/api/train/new", json={"drill_share": 2}).status_code == 400


def test_train_drill_pin_and_retry_routes(client: TestClient, tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(FAKE_SOLVER.parent))
    import fake_preflop
    from fake_tree import fake_runner

    from pn.solve import SolveCache
    from pn.solve.lazydoc import LazyDoc
    from pn.train import Trainer
    from pn.train.leaks import Leaks, library_walk

    lib = LazyDoc.open(fake_preflop.write(tmp_path / "lib.json"))
    client.app.state.trainer = Trainer(
        SolveCache(tmp_path / "cache", runner=fake_runner), background=False,
        leaks=Leaks(None, library=lambda table, depth: lib))  # fmt: skip
    spot = next(k for k in library_walk(lib).lines if "|vs_open|" in k)
    v = client.post("/api/train/new", json={"seed": 3, "drill_share": 0, "drill_spot": spot}).json()
    sid = v["sid"]
    assert v["drill"]["spot"] == spot and v["pinned_drill"]["spot"] == spot
    assert v["hand_feedback"] == [] and v["replay"] is False
    assert client.post(f"/api/train/{sid}/retry").status_code == 400  # no decision yet
    tok = next(a["token"] for a in v["legal_actions"] if a["token"] in ("call", "check", "fold"))
    a = client.post(f"/api/train/{sid}/act", json={"token": tok}).json()
    assert len(a["hand_feedback"]) == 1 and a["hand_feedback"][0]["retry"] is False
    r = client.post(f"/api/train/{sid}/retry").json()
    assert r["replay"] is True and r["history"] == v["history"] and r["result"] is None
    pin = f"/api/train/{sid}/drill"
    assert client.post(pin, json={"spot": None}).json()["pinned_drill"] is None
    assert client.post(pin, json={"spot": "nope"}).status_code == 400
    assert client.post(pin, json={"spot": "pf|CO|vs_4bet+|UTG"}).status_code == 400
    p = client.post(pin, json={"spot": spot}).json()
    assert p["pinned_drill"]["spot"] == spot and p["pinned_drill"]["label"]
    assert client.post("/api/train/nope/retry").status_code == 404
    assert client.post("/api/train/nope/drill", json={"spot": None}).status_code == 404
    assert client.post("/api/train/new", json={"drill_spot": "nope"}).status_code == 400


# ------------------------------------------------------------------ strategy: new spot / subgame
@pytest.fixture
def fake_solves(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                fake_texas: Path) -> TestClient:  # fmt: skip
    from pn.solve import SolveCache

    monkeypatch.setenv("PN_LIBRARY_DIR", str(tmp_path / "library"))
    client.app.state.solve_cache = SolveCache(tmp_path / "cache")
    return client


def _job(c: TestClient, r) -> dict:
    """Poll a 202 solve job to completion."""
    import time

    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    for _ in range(200):
        job = c.get(f"/api/solves/jobs/{jid}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {jid} did not finish")


def test_new_preflop_root_is_refused(fake_solves: TestClient) -> None:
    job = _job(fake_solves, fake_solves.post("/api/solves/new", json={"table": 6, "depth": 100}))
    assert job["status"] == "error" and "preflop_unsolvable" in job["error"]  # Plan 7
    assert fake_solves.get("/api/solves/jobs/nope").status_code == 404
    bad = fake_solves.post("/api/solves/new", json={"preset": "turbo"})
    assert bad.status_code == 400


def test_new_preflop_root_prefers_the_library(fake_solves: TestClient, tmp_path: Path,
                                              sample: dict) -> None:  # fmt: skip
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "6max_100bb_gto.json").write_text(json.dumps(sample))
    job = _job(fake_solves, fake_solves.post("/api/solves/new", json={"table": 6, "depth": 100}))
    assert job["status"] == "done" and job["name"] == "library-6max-100bb-gto"


def test_new_flop_root_and_subgame_registering(fake_solves: TestClient) -> None:
    job = _job(fake_solves, fake_solves.post("/api/solves/new", json={
        "street": "flop", "board": ["Kd", "7s", "2c"], "line": "srp", "hero": "BB",
        "seats": [{"position": "BTN"}, {"position": "BB", "range": ""}]}))  # fmt: skip
    assert job["status"] == "done", job
    name = job["name"]
    meta = fake_solves.get(f"/api/solves/{name}").json()
    assert meta["board"] == ["Kd", "7s", "2c"] and meta["parent"] is None
    assert meta["spot"] == {"table": 6, "depth": 100, "source": "gto", "street": "flop",
                            "board": ["Kd", "7s", "2c"], "line": "srp", "hero": "BB",
                            "preset": "review",  # Plan 7: the requested preset as is
                            "seats": [{"position": "BTN", "player_id": None, "range": None},
                                      {"position": "BB", "player_id": None, "range": None}]}  # fmt: skip
    assert name in {s["name"] for s in fake_solves.get("/api/solves").json()}
    leaf = fake_solves.get(f"/api/solves/{name}/walk", params={"path": "check,check"}).json()
    assert leaf["kind"] == "leaf"
    job = _job(fake_solves, fake_solves.post(f"/api/solves/{name}/subgame",
                                             json={"node_id": leaf["id"], "cards": ["As"]}))  # fmt: skip
    child = job["name"]
    assert child == f"{name}__{leaf['id']}__As"
    cm = fake_solves.get(f"/api/solves/{child}").json()
    assert cm["board"] == ["Kd", "7s", "2c", "As"] and cm["spot"] is None
    assert cm["parent"] == {"name": name, "node": leaf["id"], "cards": ["As"],
                            "path": ["check", "check"]}  # fmt: skip
    root = fake_solves.get(f"/api/solves/{child}/walk").json()
    assert root["street"] == "turn" and root["type"] == "action"
    bad = _job(fake_solves, fake_solves.post(f"/api/solves/{name}/subgame",
                                             json={"node_id": meta["root"], "cards": "As"}))  # fmt: skip
    assert bad["status"] == "error" and "does not end a street" in bad["error"]
    assert fake_solves.post("/api/solves/missing/subgame",
                            json={"node_id": "n0", "cards": "As"}).status_code == 404  # fmt: skip


def test_opponent_specific_solves_are_rejected(fake_solves: TestClient) -> None:
    """Plan 7: the trainer and root solves are GTO only (no tendencies / locks)."""
    for source in ("tendencies", "custom", "population"):
        assert fake_solves.post("/api/train/new", json={"source": source}).status_code == 400
        assert fake_solves.post("/api/solves/new", json={"source": source}).status_code == 400
    vs = fake_solves.post("/api/solves/new", json={
        "street": "flop", "board": "Kd7s2c", "line": "srp",
        "seats": [{"position": "BTN", "player_id": HERO}, {"position": "BB"}]})  # fmt: skip
    assert vs.status_code == 400




def test_train_importance_params_and_texture_pin_routes(client: TestClient, tmp_path: Path,
                                                        flop_template: Path) -> None:  # fmt: skip
    import sys

    sys.path.insert(0, str(FAKE_SOLVER.parent))
    import fake_preflop
    import flop_seed
    from fake_tree import fake_runner

    from pn.solve import SolveCache
    from pn.solve.lazydoc import LazyDoc
    from pn.train import Trainer
    from pn.train.leaks import Leaks

    flop_seed.copy_into(flop_template, tmp_path / "cache")
    lib = LazyDoc.open(fake_preflop.write(tmp_path / "lib.json"))
    db = client.app.state.store.db_path
    client.app.state.trainer = Trainer(
        SolveCache(tmp_path / "cache", runner=fake_runner), background=False, db=db,
        leaks=Leaks(db, library=lambda table, depth: lib))  # fmt: skip
    r = client.get("/api/train/importance", params={"window": 200, "p_form": 2, "p_tex": 4, "pot": "avg"})
    assert r.status_code == 200
    imp = r.json()
    assert set(imp) == {"formations", "textures", "params"}
    assert imp["params"] == {"p_form": 2, "p_tex": 4, "pot": "avg"}
    forms, texs = imp["formations"], imp["textures"]
    assert forms and set(forms[0]) == {"formation", "pot_type", "positions", "freq", "pot_bb", "score", "n",
                                       "priority", "spots"}  # fmt: skip
    assert forms[0]["priority"] == 1.0 and [f["priority"] for f in forms] == sorted(
        (f["priority"] for f in forms), reverse=True)  # fmt: skip
    assert len(texs) == 8 and texs[0]["priority"] == 1.0
    assert {"texture", "details", "freq", "pot_bb", "score", "n", "priority"} <= set(texs[0])
    assert sum(t["freq"] for t in texs) == pytest.approx(1.0, abs=1e-4)
    assert client.get("/api/train/importance", params={"p_tex": 0}).status_code == 400
    assert client.get("/api/train/importance", params={"table": 7}).status_code == 400

    v = client.post("/api/train/new", json={"seed": 5, "drill_share": 0, "p_form": 5, "p_tex": 2,
                                             "pot_mode": "avg"}).json()  # fmt: skip
    assert v["params"] == {"p_form": 5, "p_tex": 2, "pot_mode": "avg"}
    for bad in ({"p_form": 6}, {"p_tex": 0}, {"pot_mode": "mean"}, {"drill_texture": "Wet"}):
        assert client.post("/api/train/new", json=bad).status_code == 400, bad
    sid = v["sid"]
    p = client.post(f"/api/train/{sid}/params", json={"p_form": 1}).json()
    assert p["params"] == {"p_form": 1, "p_tex": 2, "pot_mode": "avg"} and p["hand_no"] == v["hand_no"]
    assert client.post(f"/api/train/{sid}/params", json={"pot_mode": "start"}).json()["params"]["pot_mode"] == "start"
    assert client.post(f"/api/train/{sid}/params", json={"p_tex": 7}).status_code == 400
    assert client.post("/api/train/nope/params", json={"p_form": 2}).status_code == 404

    pin = client.post(f"/api/train/{sid}/drill", json={"spot": None, "texture": "King"}).json()
    assert pin["pinned_drill"] == {"spot": None, "label": "King flops", "dealt": False, "texture": "King",
                                   "formation": None}  # fmt: skip
    assert client.post(f"/api/train/{sid}/drill", json={"spot": None, "texture": "Wet"}).status_code == 400
    pre = next(r["spot"] for r in client.get("/api/train/leaks").json() if r["spot"].startswith("pf|"))
    assert client.post(f"/api/train/{sid}/drill", json={"spot": pre, "texture": "Ace"}).status_code == 400
    assert client.get(f"/api/train/{sid}").json()["pinned_drill"]["texture"] == "King"  # refused: unchanged
    assert client.post(f"/api/train/{sid}/drill", json={"spot": None, "texture": None}).json()["pinned_drill"] is None
    form = forms[-1]["formation"]
    pin = client.post(f"/api/train/{sid}/drill", json={"spot": None, "formation": form}).json()["pinned_drill"]  # fmt: skip
    assert pin == {"spot": None, "label": form, "dealt": False, "texture": None, "formation": form}
    for bad in ({"formation": "CO BB"}, {"formation": form, "spot": pre}):
        assert client.post(f"/api/train/{sid}/drill", json=bad).status_code == 400, bad
    assert client.post("/api/train/new", json={"drill_formation": "nope"}).status_code == 400
    v = client.post("/api/train/new", json={"seed": 6, "drill_formation": form}).json()
    assert v["pinned_drill"]["formation"] == form and v["pinned_drill"]["dealt"] is True
