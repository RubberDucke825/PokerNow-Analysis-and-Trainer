"""Line breakdown (pn/web/study.py line_breakdown, GET /api/solves/{name}/line_breakdown): per
action node on the line, the actor's per-class reach and its split over the actions; and the
trainer recording the previous street's solve as `parent` of a lifted street."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fake_tree import fake_doc, fake_runner
from fastapi.testclient import TestClient

from pn import ranges
from pn.solve import SolveCache
from pn.train import Config, Trainer
from pn.web import study
from pn.web.app import create_app

SPOT = {"game": {"street": "turn", "board": ["Jc", "5h", "4c", "2d"], "pot": 550, "sb": 50, "bb": 100},
        "players": [{"name": "sb", "position": "SB", "stack": 9750, "range": "AA,KK,QQ,65s,JTs,AKo"},
                    {"name": "bb", "position": "BB", "stack": 9750, "range": "AKs,AQs,77,J5s,KQo"}],
        "sizes": {}}  # fmt: skip
TOL = 1e-3


def _doc(seed: int = 1) -> dict:
    """fake_doc with random per-combo strategies, walked reach (stored only where each range
    starts) and a chance node (river 3s / 9h) under check-check."""
    doc = fake_doc(SPOT)
    rnd = random.Random(seed)
    nodes = doc["nodes"]
    root = nodes[doc["root"]]
    first = {doc["root"], root["actions"][0]["child"]}  # each player's range: its first node
    for nid, node in nodes.items():
        node.pop("reach_by_player", None)
        if nid not in first:
            node.pop("reach", None)
        elif "reach" in node:  # a real document's ranges have the board's combos zeroed
            node["reach"] = [0.0 if {c[:2], c[2:]} & set(SPOT["game"]["board"]) else w
                             for c, w in zip(doc["combos"], node["reach"], strict=True)]  # fmt: skip
        if node.get("type") == "action":
            k = len(node["actions"])
            rows = []
            for _ in node["strategy"]:
                xs = [rnd.random() for _ in range(k)]
                rows.append([x / sum(xs) for x in xs])
            node["strategy"] = rows
    cc = nodes[next(a["child"] for a in root["actions"] if a["token"] == "check")]
    leaf = next(a["child"] for a in cc["actions"] if a["token"] == "check")
    river = {c: f"r{c}" for c in ("3s", "9h")}
    for nid in river.values():
        nodes[nid] = {**{k: v for k, v in root.items() if k not in ("actions", "reach")},
                      "actions": [{"token": t, "child": leaf} for t in ("check", "bet50")]}
    nodes["chance"] = {"type": "chance", "children": river}
    cc["actions"] = [{**a, "child": "chance"} if a["token"] == "check" else a for a in cc["actions"]]
    return doc


def _path(doc: dict, tokens: list[str]) -> str:
    nid = doc["root"]
    for t in tokens:
        node = doc["nodes"][nid]
        nid = node["children"][t] if node["type"] == "chance" else \
            next(a["child"] for a in node["actions"] if a["token"] == t)  # fmt: skip
    return nid


def _taken(step: dict) -> float:
    k = step["actions"].index(step["taken"])
    return sum(c["reach"] * c["freq"][k] for c in step["classes"].values())


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    solves = tmp_path / "solves"
    solves.mkdir()
    return TestClient(create_app(tmp_path / "none.sqlite", solves))


def test_steps_split_each_actors_reach_and_chain_to_the_node(client: TestClient) -> None:
    doc = _doc()
    reg = client.app.state.registry
    reg.register("turn-a", doc, "test", parent={"name": "flop-x", "node": "n7"})
    tokens = ["check", "check", "9h", "bet50"]
    node = _path(doc, tokens)
    r = client.get("/api/solves/turn-a/line_breakdown", params={"node": node})
    assert r.status_code == 200
    out = r.json()
    assert out["board"] == ["Jc", "5h", "4c", "2d", "9h"]
    assert out["parent"] == {"name": "flop-x", "node": "n7"}
    steps = out["steps"]
    assert [s["position"] for s in steps] == ["SB", "BB", "SB"]
    assert [s["taken"] for s in steps] == ["check", "check", "bet50"]
    assert steps[0]["actions"] == ["check", "bet50"]
    for s in steps:
        assert len(s["classes"]) == 169 and set(s["classes"]) == set(ranges.all_classes())
        for c in s["classes"].values():
            assert len(c["freq"]) == len(s["actions"])
            if c["reach"]:
                assert sum(c["freq"]) == pytest.approx(1, abs=TOL)
                assert sum(c["reach"] * f for f in c["freq"]) == pytest.approx(c["reach"], rel=TOL)
            else:
                assert c["freq"] == [0] * len(s["actions"])
    # live combos: board blockers on the turn, the dealt river card from then on
    assert steps[0]["classes"]["JTs"]["n"] == 3 and steps[0]["classes"]["AKo"]["n"] == 12
    assert steps[2]["classes"]["99"]["n"] == 3 and steps[1]["classes"]["99"]["n"] == 6
    assert steps[0]["classes"]["AA"]["reach"] == pytest.approx(6)  # range weight at the root
    # the taken slice is the actor's reach at its next decision / at the final node
    assert _taken(steps[0]) == pytest.approx(sum(c["reach"] for c in steps[2]["classes"].values()), rel=TOL)
    final = study.node_ranges(doc, tokens)["players"]
    assert _taken(steps[2]) == pytest.approx(sum(final[0]["reach"]), rel=TOL)
    assert _taken(steps[1]) == pytest.approx(sum(final[1]["reach"]), rel=TOL)


def test_root_has_no_steps_and_unknown_solve_or_node_is_404(client: TestClient) -> None:
    client.app.state.registry.register("turn-b", _doc(), "test")
    out = client.get("/api/solves/turn-b/line_breakdown", params={"node": _doc()["root"]}).json()
    assert out["steps"] == [] and out["parent"] is None and out["board"] == SPOT["game"]["board"]
    assert client.get("/api/solves/turn-b/line_breakdown", params={"node": "nope"}).status_code == 404
    assert client.get("/api/solves/nope/line_breakdown", params={"node": "n0"}).status_code == 404


def test_lifted_street_records_the_previous_solve_as_parent(flop_template: Path, tmp_path: Path) -> None:
    import flop_seed

    flop_seed.copy_into(flop_template, tmp_path / "cache")
    calls: list[tuple[str, dict, dict | None]] = []

    def register(doc: dict, req, parent: dict | None) -> str:
        calls.append((f"train-{len(calls)}", doc, parent))
        return calls[-1][0]

    t = Trainer(SolveCache(tmp_path / "cache", runner=fake_runner), background=False,
                register=register)  # fmt: skip
    v = t.new(Config(seed=11, hero_seat="BB"))
    for _ in range(60):
        while v["result"] is None and not any(p for _, _, p in calls):
            toks = [o["token"] for o in v["legal_actions"]]
            v = t.act(v["sid"], next(x for x in ("check", "call", "fold") if x in toks))
        if any(p for _, _, p in calls):
            break
        v = t.next(v["sid"])
    lifted = [(n, p) for n, _, p in calls if p]
    assert lifted, "never lifted a street"
    name, parent = lifted[0]
    docs = {n: d for n, d, _ in calls}
    assert parent["name"] in docs and parent["name"] != name
    assert docs[parent["name"]]["nodes"][parent["node"]].get("kind") == "leaf"
