"""spot_key, SolveCache (hit/miss, disk, LRU, one solve at a time), backend selection and the
texas as the only backend (fake console_solver), presets and stale documents."""

from __future__ import annotations

import copy
import logging
import threading
import time
from pathlib import Path

import pytest

from pn.solve import SolveCache, SolveError, build_spot, choose_backend, spot_key
from pn.solve import backend as be
from pn.solve.request import Budget, new_request, parse_board


def _flop_spot(board: str = "Kd2cKh", **kw) -> dict:
    return build_spot(new_request(["BTN", "BB"], 100, board=parse_board(board), line="srp",
                                  hero="BTN", **kw))  # fmt: skip


# ------------------------------------------------------------------ spot_key


def test_spot_key_ignores_key_order_and_range_spelling() -> None:
    spot = _flop_spot()
    shuffled = copy.deepcopy(spot)
    shuffled["game"] = dict(reversed(list(spot["game"].items())))
    shuffled["sizes"] = dict(reversed(list(spot["sizes"].items())))
    shuffled["players"] = [dict(reversed(list(p.items()))) for p in spot["players"]]
    assert spot_key(shuffled) == spot_key(spot)
    respelled = copy.deepcopy(spot)
    respelled["players"][0]["range"] = ",".join(reversed(spot["players"][0]["range"].split(",")))
    assert spot_key(respelled) == spot_key(spot)


def test_spot_key_changes_with_content() -> None:
    spot = _flop_spot()
    changed = copy.deepcopy(spot)
    changed["players"][0]["range"] = "AA,KK"
    assert spot_key(changed) != spot_key(spot)
    assert spot_key(_flop_spot("Kd2cKs")) != spot_key(spot)
    assert spot_key(_flop_spot()) == spot_key(spot)
    assert len(spot_key(spot)) == 64


# ------------------------------------------------------------------ SolveCache


class FakeRunner:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[str] = []
        self.active = self.max_active = 0
        self.delay = delay
        self._lock = threading.Lock()

    def __call__(self, spot: dict, backend: str | None) -> tuple[dict, str]:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(spot_key(spot))
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        return {"meta": {}, "nodes": {}, "board": spot["game"]["board"]}, backend or "texas"


def test_cache_miss_then_memory_and_disk_hits(tmp_path: Path) -> None:
    runner = FakeRunner()
    cache = SolveCache(tmp_path, runner=runner)
    spot = _flop_spot()
    key = spot_key(spot)
    assert cache.get(key) is None
    doc = cache.solve(spot)
    assert doc["meta"]["backend"] == "texas"
    assert cache.solve(spot) is doc and len(runner.calls) == 1
    assert cache.path(key).exists() and (tmp_path / f"{key}.toml").exists()
    fresh = SolveCache(tmp_path, runner=runner)
    assert fresh.get(key) == doc and fresh.solve(spot) == doc and len(runner.calls) == 1


def test_cache_memory_is_lru_of_four(tmp_path: Path) -> None:
    cache = SolveCache(tmp_path, runner=FakeRunner())
    boards = ["Kd2cKh", "Ad2cKh", "Qd2cKh", "Jd2cKh", "Td2cKh"]
    for b in boards:
        cache.solve(_flop_spot(b))
    assert len(cache._mem) == 4 and spot_key(_flop_spot(boards[0])) not in cache._mem


def test_cache_runs_one_solve_at_a_time(tmp_path: Path) -> None:
    runner = FakeRunner(delay=0.05)
    cache = SolveCache(tmp_path, runner=runner)
    spots = [_flop_spot(b) for b in ("Kd2cKh", "Ad2cKh", "Qd2cKh")] + [_flop_spot("Kd2cKh")]
    threads = [threading.Thread(target=cache.solve, args=(s,)) for s in spots]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert runner.max_active == 1
    assert len(runner.calls) == 3  # the duplicate spot waited and hit the cache


# ------------------------------------------------------------------ backends


def test_choose_backend_table() -> None:
    assert choose_backend(_flop_spot()) == "texas"
    turn = build_spot(new_request(["BTN", "BB"], 100, board=parse_board("Kd2cKh7s"), line="3bp"))
    assert choose_backend(turn) == "texas"
    for pre in (build_spot(new_request(["BTN", "BB"], 100)),
                build_spot(new_request(["CO", "BTN", "BB"], 100))):
        with pytest.raises(SolveError, match="preflop_unsolvable"):
            choose_backend(pre)  # Plan 7: no preflop solver
    three = build_spot(new_request(["CO", "BTN", "BB"], 100, board=parse_board("Kd2cKh"), line="srp"))
    with pytest.raises(SolveError, match="multiway_unsolvable"):
        choose_backend(three)  # 2026-10-07: 2-player only


def test_multiway_and_preflop_never_run_a_solver(fake_texas: Path, tmp_path: Path) -> None:
    three = build_spot(new_request(["CO", "BTN", "BB"], 100, board=parse_board("Kd2cKh"), line="srp"))
    with pytest.raises(SolveError, match="multiway_unsolvable"):
        SolveCache(tmp_path / "c").solve(three)
    with pytest.raises(SolveError, match="preflop_unsolvable"):
        SolveCache(tmp_path / "c").solve(build_spot(new_request(["BTN", "BB"], 100)))
    assert not fake_texas.exists()  # no binary ran


def test_texas_runs_by_default(fake_texas: Path, tmp_path: Path,
                               monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    args = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_ARGS_LOG", str(args))
    seen: list[dict] = []
    doc = SolveCache(tmp_path / "c").solve(_flop_spot(), progress=seen.append)
    assert doc["meta"]["backend"] == "texas" and fake_texas.read_text().split() == ["texas"]
    assert args.read_text().split()[:2] == ["-i", "spot.txt"]
    assert doc["meta"]["exploitability_pct_pot"] == 0.4 and doc["meta"]["stop"] == "converged"
    assert [d["iterations"] for d in seen] == [0, 4]
    root = doc["nodes"][doc["root"]]
    assert root["actions"][0]["token"] == "check" and root["actions"][1]["token"].startswith("bet")


def test_texas_missing_raises_and_caches_nothing(tmp_path: Path) -> None:
    cache = SolveCache(tmp_path / "c")
    with pytest.raises(SolveError, match="console_solver not found"):
        cache.solve(_flop_spot())
    assert not list((tmp_path / "c").glob("*.json*"))


def test_texas_failure_raises(fake_texas: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_TEXAS_MODE", "fail")
    with pytest.raises(SolveError, match="texas exit 1: boom") as e:
        be.run_backend(_flop_spot())
    assert e.value.code == 1


def test_preempted_solve_caches_nothing(fake_texas: Path, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setenv("FAKE_TEXAS_MODE", "sleep")
    flag = threading.Event()
    threading.Timer(0.3, flag.set).start()
    with pytest.raises(be.Preempted):
        SolveCache(tmp_path / "c").solve(_flop_spot(), should_stop=flag.is_set)
    assert not list((tmp_path / "c").glob("*.json*"))


def test_explicit_backend_and_unknown(fake_texas: Path) -> None:
    assert be.run_backend(_flop_spot(), "texas")[1] == "texas"
    with pytest.raises(ValueError, match="unknown backend"):
        be.run_backend(_flop_spot(), "dcfr")


# ------------------------------------------------------------------ solve presets


def test_solve_presets_write_budget_and_cap() -> None:
    from pn.solve.request import SOLVE_PRESETS, apply_preset

    spot = _flop_spot()
    lib, b_lib = apply_preset(spot, "library")
    rev, b_rev = apply_preset(spot, "review")
    tr, b_tr = apply_preset(spot, "trainer")
    assert (b_lib, b_rev, b_tr) == ("texas", "texas", "texas")
    assert lib["sizes"]["max_bets_per_street"] == 2 and lib["game"]["time_limit_sec"] == 3600
    assert lib["game"]["max_iterations"] == 400 and lib["game"]["target_exploitability"] == 0.3
    assert lib["sizes"]["flop_bets"] == [33]  # Plan 10: first sizing-table size of the bucket
    assert rev["sizes"]["flop_bets"] == "auto" and tr["sizes"]["flop_bets"] == "auto"
    assert rev["sizes"]["max_bets_per_street"] == 2 and rev["game"]["max_iterations"] == 600
    assert tr["game"]["max_iterations"] == 300 and tr["game"]["target_exploitability"] == 0.5
    assert tr["sizes"]["max_bets_per_street"] == spot["sizes"]["max_bets_per_street"]
    assert len({spot_key(lib), spot_key(rev), spot_key(tr)}) == 3
    assert spot["game"]["time_limit_sec"] == 300  # input not mutated
    assert Budget.preset("review") == SOLVE_PRESETS["review"].budget
    with pytest.raises(ValueError, match="preset"):
        apply_preset(spot, "turbo")


def test_presets_refuse_multiway_postflop_and_preflop() -> None:
    from pn.solve.request import apply_preset

    three = build_spot(new_request(["CO", "BTN", "BB"], 100, board=parse_board("Kd2cKh"), line="srp"))
    pre = build_spot(new_request(["BTN", "BB"], 100))
    for name in ("trainer", "review", "library"):
        with pytest.raises(SolveError, match="multiway_unsolvable"):
            apply_preset(three, name)
        with pytest.raises(SolveError, match="preflop_unsolvable"):
            apply_preset(pre, name)


# ------------------------------------------------------------------ stale backends (2026-10-07)


def _cached(tmp_path: Path, backend: str, **meta) -> tuple[SolveCache, dict, FakeRunner]:
    """A cache holding the heads-up flop spot solved by `backend`, then a runner for re-solves."""
    spot = _flop_spot()
    old = SolveCache(tmp_path, runner=lambda s, b: ({"meta": {"backend": backend, **meta},
                                                     "nodes": {}}, backend))  # fmt: skip
    old.solve(spot)
    runner = FakeRunner()
    return SolveCache(tmp_path, runner=runner), spot, runner


@pytest.mark.parametrize("old", ["hu", "mwsolver", "dcfr"])
def test_stale_heads_up_doc_is_resolved_once_texas_is_available(
        old: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:  # fmt: skip
    from pn.solve import cache as cache_mod

    cache, spot, runner = _cached(tmp_path, old)
    assert cache.solve(spot)["meta"]["backend"] == old  # texas not available: still a hit
    assert runner.calls == []
    monkeypatch.setattr(cache_mod, "texas_available", lambda: True)
    with caplog.at_level(logging.WARNING, logger="pn.solve.cache"):
        doc = cache.solve(spot)
    assert len(runner.calls) == 1 and doc["meta"]["backend"] == "texas"
    assert any("stale backend" in r.getMessage() for r in caplog.records)
    assert cache.path(spot_key(spot)).exists()  # same key, document replaced


def test_stale_rule_exemptions(monkeypatch: pytest.MonkeyPatch) -> None:
    from pn.solve import cache as cache_mod
    from pn.solve.cache import is_stale

    monkeypatch.setattr(cache_mod, "texas_available", lambda: True)
    hu_flop = _flop_spot()
    old = {"meta": {"backend": "dcfr"}}
    assert is_stale(hu_flop, None, old) and is_stale(hu_flop, "texas", old)
    assert not is_stale(hu_flop, None, {"meta": {"backend": "texas"}})
    old_fallback = {"meta": {"backend": "mwsolver", "backend_fallback": "dcfr->mwsolver"}}
    assert is_stale(hu_flop, None, old_fallback)
    assert not is_stale(build_spot(new_request(["BTN", "BB"], 100)), None, old)  # preflop
    monkeypatch.setattr(cache_mod, "texas_available", lambda: False)
    assert not is_stale(hu_flop, None, old)
