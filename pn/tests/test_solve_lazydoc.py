"""LazyDoc (offset-indexed large documents) behaves like the dict document for every reader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_solve_subgame import FLOP, _flop_doc, _hint

from pn import ranges
from pn.solve import lazydoc as ld
from pn.solve import subgame
from pn.solve.subgame import path_to
from pn.web import solves as ws

SIX = ("LJ", "HJ", "CO", "BTN", "SB", "BB")


def preflop_doc() -> dict:
    """6-max root: RFI chain LJ..SB (raise 20%, limp 0, fold 80%); behind each open a chain of
    defenders (3bet 10%, call 15%, fold 75%) ending back at the opener."""
    nodes: dict[str, dict] = {}
    players = [{"index": i, "name": p.lower(), "position": p, "hero": p == "BTN"}
               for i, p in enumerate(SIX)]  # fmt: skip
    nid = iter(f"n{i}" for i in range(1000))

    def action(player: int, summary: dict, children: dict) -> str:
        k = next(nid)
        nodes[k] = {"type": "action", "street": "preflop", "player": player, "pot": 150,
                    "actions": [{"token": t, "child": c} for t, c in children.items()],
                    "summary": summary}  # fmt: skip
        return k

    def terminal() -> str:
        k = next(nid)
        nodes[k] = {"type": "terminal", "kind": "leaf", "pot": 150}
        return k

    def defenders(opener: int) -> str:
        nxt = action(opener, {"fold": 1.0}, {"fold": terminal()})  # back to the opener
        for p in reversed(range(opener + 1, len(SIX))):
            nxt = action(p, {"fold": 0.75, "call": 0.15, "raise1000": 0.10},
                         {"fold": nxt, "call": terminal(), "raise1000": terminal()})  # fmt: skip
        return nxt

    nxt = terminal()  # folded to the BB
    for p in reversed(range(len(SIX) - 1)):
        nxt = action(p, {"fold": 0.8, "call": 0.0, "raise300": 0.15, "raise600": 0.05},
                     {"fold": nxt, "call": terminal(), "raise300": defenders(p),
                      "raise600": terminal()})  # fmt: skip
    return {"meta": {"iterations": 77, "exploitability_pct_pot": 0.9, "stop": "time_limit"},
            "players": players, "combos": list(ranges.all_combos()), "root": nxt, "nodes": nodes}  # fmt: skip


def _write(tmp_path: Path, doc: dict, name: str = "doc.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc, separators=(",", ":")))
    return path


@pytest.fixture(autouse=True)
def always_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws, "LAZY_BYTES", 0)


def test_lazy_doc_matches_dict(tmp_path: Path) -> None:
    doc = preflop_doc()
    doc["meta"]["config_path"] = 'C:\\\\odd {dir} "quoted"/x.toml'  # braces, quotes, escapes
    lazy = ws.open_doc(_write(tmp_path, doc))
    assert isinstance(lazy, ld.LazyDoc)
    assert lazy.meta == doc["meta"] and lazy.root == doc["root"] and lazy["players"] == doc["players"]
    assert len(lazy["nodes"]) == len(doc["nodes"]) and set(lazy["nodes"]) == set(doc["nodes"])
    for nid in list(doc["nodes"])[:: max(1, len(doc["nodes"]) // 20)]:
        assert lazy["nodes"][nid] == doc["nodes"][nid]
    leaf = next(n for n, v in doc["nodes"].items() if v["type"] == "terminal")
    assert path_to(lazy, leaf) == path_to(doc, leaf)


def test_index_is_cached_and_rebuilt_when_the_file_changes(tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    path = _write(tmp_path, preflop_doc())
    ld.LazyDoc.open(path)
    assert ld.index_path(path).exists()
    calls = []
    real = ld.build_index
    monkeypatch.setattr(ld, "build_index", lambda p: calls.append(p) or real(p))
    ld.LazyDoc.open(path)
    assert calls == []
    _write(tmp_path, {**preflop_doc(), "meta": {"stop": "converged"}})
    assert ld.LazyDoc.open(path).meta == {"stop": "converged"} and len(calls) == 1


def test_dangling_child_and_missing_root_fail_validation(tmp_path: Path) -> None:
    doc = preflop_doc()
    dropped = next(n for n, v in doc["nodes"].items() if v["type"] == "terminal")
    del doc["nodes"][dropped]
    with pytest.raises(ws.SolveError, match="child ids missing"):
        ws.open_doc(_write(tmp_path, doc, "a.json"))
    with pytest.raises(ws.SolveError, match="nodes"):
        ws.open_doc(_write(tmp_path, {"meta": {}, "combos": []}, "b.json"))


def test_subgame_and_chance_edges_on_lazy_doc(tmp_path: Path) -> None:
    doc = _flop_doc()
    doc["nodes"]["n1"]["actions"].append({"token": "check", "child": "n6"})
    doc["nodes"]["n6"] = {"type": "chance", "street": "turn", "children": {"8h": "n7", "9h": "n8"}}
    doc["nodes"]["n7"] = {"type": "terminal", "kind": "fold", "pot": 1}
    doc["nodes"]["n8"] = {"type": "terminal", "kind": "fold", "pot": 1}
    lazy = ws.open_doc(_write(tmp_path, doc))
    assert lazy.parent_of("n8") == ("n6", "9h") and lazy.parent_of("n4") == ("n3", "call")
    assert subgame(lazy, "n4", ("8h",), _hint()) == subgame(doc, "n4", ("8h",), _hint())
    assert subgame(lazy, "n4", ("8h",), _hint())["game"]["board"] == [*FLOP, "8h"]


def test_registry_serves_lazy_library_nodes(tmp_path: Path) -> None:
    from pn.solve import SolveCache

    path = _write(tmp_path, preflop_doc())
    reg = ws.Registry(tmp_path / "solves", SolveCache(tmp_path / "cache"))
    reg.register_library("library-x", path, "x")
    meta = reg.meta("library-x")
    assert meta["node_count"] == len(preflop_doc()["nodes"]) and len(meta["combos"]) == 1326
    root = preflop_doc()["root"]
    assert reg.node("library-x", root)["summary"]["fold"] == 0.8
    assert reg.walk("library-x", ["fold"])["player"] == 1
