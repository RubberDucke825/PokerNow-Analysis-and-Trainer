"""Generated solves (web "New spot" / subgames) persist by SolveCache key: a name evicted from
memory, or asked for after a server restart, reloads from the cache with its request."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pn import ranges
from pn.solve import SolveCache, build_spot, docstore, spot_key
from pn.solve.request import new_request, parse_board
from pn.web import solves as ws


def _doc(board: list[str]) -> dict:
    return {"meta": {}, "players": [], "combos": list(ranges.all_combos()), "root": "n0",
            "nodes": {"n0": {"type": "terminal", "kind": "fold", "pot": 1}}, "board": board}  # fmt: skip


def _solved(cache: SolveCache, board: str) -> tuple[str, dict, object]:
    req = new_request(["BTN", "BB"], 100, board=parse_board(board), line="srp", hero="BTN")
    spot = build_spot(req)
    doc = cache.solve(spot)
    return spot_key(spot), doc, req


@pytest.fixture
def cache(tmp_path: Path) -> SolveCache:
    return SolveCache(tmp_path / "cache", runner=lambda spot, b: (_doc(spot["game"]["board"]), "hu"))


def test_generated_name_survives_restart(cache: SolveCache, tmp_path: Path) -> None:
    key, doc, req = _solved(cache, "Kd2cKh")
    reg = ws.Registry(tmp_path / "solves", cache)
    parent = {"name": "root", "node": "n3", "cards": ["Kh"], "path": []}
    reg.register("new-a", ws.with_board(doc, req.board), "solved flop root", req=req, key=key,
                 parent=parent)  # fmt: skip
    restarted = ws.Registry(tmp_path / "solves", cache)
    assert restarted.has("new-a")
    assert restarted.get("new-a")["meta"]["board"] == ["Kd", "2c", "Kh"]
    info = restarted.info("new-a")
    assert info["req"] == req and info["parent"] == parent
    assert "new-a" in {r["name"] for r in ws.Registry(tmp_path / "solves", cache).list()}


def test_evicted_name_reloads(cache: SolveCache, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws, "MAX_GENERATED", 2)
    reg = ws.Registry(tmp_path / "solves", cache)
    names = []
    for i, board in enumerate(("Kd2cKh", "Ad2cKh", "Qd2cKh")):
        key, doc, req = _solved(cache, board)
        names.append(reg.register(f"n{i}", ws.with_board(doc, req.board), "x", req=req, key=key))
    assert "n0" not in reg._generated  # evicted from memory
    assert reg.get("n0")["meta"]["board"] == ["Kd", "2c", "Kh"]


def test_unindexed_and_unpersisted_names(cache: SolveCache, tmp_path: Path) -> None:
    _key, doc, req = _solved(cache, "Kd2cKh")
    reg = ws.Registry(tmp_path / "solves", cache)
    reg.register("mem-only", doc, "trainer street solve", req=req)  # no key: memory only
    again = ws.Registry(tmp_path / "solves", cache)
    assert not again.has("mem-only")
    with pytest.raises(ws.SolveNotFound):
        again.get("mem-only")


# ------------------------------------------------------------------ library roots


def _lib(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "library" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_doc([])))
    return path


def test_library_roots_are_lazy_and_one_at_a_time(cache: SolveCache, tmp_path: Path) -> None:
    reg = ws.Registry(tmp_path / "solves", cache)
    a, b = _lib(tmp_path, "6max_100bb_gto"), _lib(tmp_path, "6max_200bb_gto")
    reg.register_library("library-a", a, "library a")
    reg.register_library("library-b", b, "library b")
    assert reg._library is None and reg.has("library-a")  # registered, not loaded
    assert reg.get("library-a")["root"] == "n0" and reg._library[0] == "library-a"
    reg.get("library-b")
    assert reg._library[0] == "library-b"  # replaced: only one library root in memory
    assert not reg._cache and not reg._generated  # outside the 4-doc LRU and generated store
    assert {"library-a", "library-b"} <= {r["name"] for r in reg.list()}
    restarted = ws.Registry(tmp_path / "solves", cache)
    assert restarted.has("library-a") and restarted._library is None  # has() does not load
    assert restarted.get("library-a")["root"] == "n0" and restarted._library[0] == "library-a"
    a.unlink()
    again = ws.Registry(tmp_path / "solves", cache)
    assert not again.has("library-a")
    with pytest.raises(ws.SolveNotFound):
        again.get("library-a")


def _hu_doc(backend: str, pot: int) -> dict:
    return {"meta": {"backend": backend}, "combos": list(ranges.all_combos()), "root": "n0",
            "players": [{"position": "BTN", "hero": True}, {"position": "BB"}],
            "nodes": {"n0": {"type": "terminal", "kind": "fold", "street": "flop", "pot": pot}}}  # fmt: skip


def test_stale_hu_document_is_replaced_by_the_resolved_cache_entry(
    cache: SolveCache, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 2-player postflop document from hu/mwsolver is swapped (same name, same board) for the
    cache entry of its key once SolveCache has re-solved that entry with texas."""
    import os

    from pn.solve import cache as cache_mod

    monkeypatch.setattr(cache_mod, "texas_available", lambda: True)
    reg = ws.Registry(tmp_path / "solves", cache)
    key = "k" * 16
    cache.root.mkdir(parents=True, exist_ok=True)
    path = docstore.dump(cache.root, key, _hu_doc("hu", 1))
    reg.register("new-hu", ws.with_board(_hu_doc("hu", 1), ("Kd", "2c", "Kh")), "x", key=key)
    assert reg.get("new-hu")["meta"]["backend"] == "hu"  # cache entry still stale: kept
    docstore.dump(cache.root, key, _hu_doc("texas", 2))
    os.utime(path, (1, 1))  # a new mtime even on coarse filesystem clocks
    doc = reg.get("new-hu")
    assert doc["meta"]["backend"] == "texas" and doc["nodes"]["n0"]["pot"] == 2
    assert doc["meta"]["board"] == ["Kd", "2c", "Kh"]
    assert reg.walk("new-hu", [])["pot"] == 2


def test_hu_document_kept_without_texas(cache: SolveCache, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    from pn.solve import cache as cache_mod

    monkeypatch.setattr(cache_mod, "texas_available", lambda: False)
    reg = ws.Registry(tmp_path / "solves", cache)
    key = "j" * 16
    cache.root.mkdir(parents=True, exist_ok=True)
    docstore.dump(cache.root, key, _hu_doc("texas", 2))
    reg.register("new-hu", _hu_doc("hu", 1), "x", key=key)
    assert reg.get("new-hu")["meta"]["backend"] == "hu"
