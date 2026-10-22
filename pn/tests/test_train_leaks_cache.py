"""Plan 14 P0: the SolveCache scan behind Leaks (postflop conditionals) is built once and reused
until the cache directory changes; callers that must not block (hand ends, background deals)
never scan inline."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from pn.solve import SolveCache
from pn.solve.lazydoc import LazyDoc
from pn.train import Config, Trainer, leaks
from pn.train.leaks import DocStats, Leaks

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import fake_preflop
from fake_tree import fake_runner

FLOP = (DocStats("flop", "srp", False, {"first": 1.0}, 0.5),
        DocStats("flop", "srp", True, {"facing_bet": 0.4}, 0.5))  # fmt: skip


@pytest.fixture
def counts(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Counting stubs: doc_stats (one per entry read) and conditionals (one per scan)."""
    n = {"doc_stats": 0, "conditionals": 0}
    real = leaks.conditionals

    def doc_stats(path: Path) -> tuple[DocStats, ...]:
        n["doc_stats"] += 1
        return FLOP

    def conditionals(stats: list[DocStats]) -> dict[str, float]:
        n["conditionals"] += 1
        return real(stats)

    monkeypatch.setattr(leaks, "doc_stats", doc_stats)
    monkeypatch.setattr(leaks, "conditionals", conditionals)
    return n


def _entry(root: Path, name: str) -> None:
    (root / f"{name}.toml").write_text("")
    st = root.stat()  # a later mtime even on a coarse clock
    os.utime(root, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _model(root: Path, lib=None) -> Leaks:
    return Leaks(None, library=lambda t, d: lib, cache_root=root, freqs={})


def _until(cond, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def test_one_scan_across_many_calls_and_a_rescan_when_the_cache_changes(
    tmp_path: Path, counts: dict[str, int]
) -> None:
    for k in range(3):
        _entry(tmp_path, f"e{k}")
    model = _model(tmp_path)
    first = model.rows(None, 6, 100, 1000)
    for _ in range(20):
        assert model.rows(None, 6, 100, 1000) == first
        model.lines(6, 100, "post|srp|oop|flop|first")
        model.solver_map(6, 100)
    assert counts == {"doc_stats": 3, "conditionals": 1}
    assert "post|srp|ip|flop|facing_bet" in model.postflop()
    _entry(tmp_path, "e3")  # a new solve lands: only it is read
    model.rows(None, 6, 100, 1000)
    model.rows(None, 6, 100, 1000)
    assert counts == {"doc_stats": 4, "conditionals": 2}
    (tmp_path / "e0.toml").unlink()  # a removed entry drops out
    model.rows(None, 6, 100, 1000)
    assert counts["conditionals"] == 3 and len(model._docs) == 3


def test_wait_false_never_scans_inline(tmp_path: Path, counts: dict[str, int],
                                       monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    _entry(tmp_path, "e0")
    gate = threading.Event()
    stub = leaks.doc_stats

    def slow(path: Path) -> tuple[DocStats, ...]:
        gate.wait(5)
        return stub(path)

    monkeypatch.setattr(leaks, "doc_stats", slow)
    model = _model(tmp_path)
    threading.Thread(target=model.warm, daemon=True).start()
    _until(lambda: model._scan_lock.locked())
    t0 = time.monotonic()
    model.rows(None, 6, 100, 1000, wait=False)  # cold: preflop + fallbacks only
    assert time.monotonic() - t0 < 1.0 and not model.ready()
    assert model.postflop(wait=False) is None
    gate.set()
    _until(model.ready)
    assert "post|srp|ip|flop|facing_bet" in model.postflop(wait=False)
    assert counts["conditionals"] == 1
    # a change: wait=False answers from the last scan and refreshes in the background
    _entry(tmp_path, "e1")
    before = model.postflop(wait=False)
    assert before is not None
    _until(lambda: counts["conditionals"] == 2 and not model._scan_lock.locked())
    assert counts["doc_stats"] == 2


def test_background_trainer_drills_while_warming(
    tmp_path: Path, counts: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cache"
    root.mkdir()
    _entry(root, "e0")
    gate = threading.Event()
    stub = leaks.doc_stats
    monkeypatch.setattr(leaks, "doc_stats", lambda p: (gate.wait(5), stub(p))[1])
    lib = LazyDoc.open(fake_preflop.write(tmp_path / "lib.json"))
    model = _model(root, lib)
    threading.Thread(target=model.warm, daemon=True).start()
    _until(lambda: model._scan_lock.locked())
    db = tmp_path / "pn.sqlite"
    t = Trainer(SolveCache(root, runner=fake_runner), background=True, db=db, leaks=model)
    t0 = time.monotonic()
    v = t.new(Config(seed=3, drill_share=1.0))
    assert time.monotonic() - t0 < 2.0
    # weighted drills never wait for the scan: they draw from the fallback frequencies
    assert v["drill"] is not None and not model.ready()
    pre = next(k for k in leaks.library_walk(lib).lines if "|vs_open|" in k)
    assert t.new(Config(seed=3, drill_spot=pre))["drill"]["spot"] == pre  # pins need no scan
    gate.set()
    _until(model.ready)
    assert t.new(Config(seed=5, drill_share=1.0))["drill"] is not None
    assert counts["conditionals"] == 1


def test_an_entry_scanned_before_its_json_lands_counts_once_it_does(tmp_path: Path) -> None:
    """SolveCache writes <key>.toml, then <key>.json: a scan between the two must not remember
    the entry as unreadable."""
    from pn.solve import build_spot
    from pn.solve.request import new_request, parse_board

    src, root = tmp_path / "src", tmp_path / "cache"
    root.mkdir()
    SolveCache(src, runner=fake_runner).solve(
        build_spot(new_request(["BTN", "BB"], 100, board=parse_board("Kd7s2c"), line="srp"))
    )
    (toml,) = src.glob("*.toml")
    model = _model(root)
    (root / toml.name).write_text(toml.read_text())
    _entry(root, "bump")  # the toml alone (plus an empty entry) changes the stamp
    assert model.postflop() == {}
    (root / f"{toml.stem}.json").write_text(json.dumps(SolveCache(src).get(toml.stem)))
    _entry(root, "bump2")
    assert model.postflop() == _model(root).postflop() != {}
