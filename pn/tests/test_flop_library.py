"""Library-first heads-up flops (pn.train.flop_library) and the library fill worker
(pn.train.library_fill): matchups from the trainer's preflop engine, the flop index, board
picks, texture-stratified flops, burn ordering, resumable / preemptible fills, the cross-process
solve lock and the CLI."""

from __future__ import annotations

import json
import random
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import flop_seed
import pytest
from typer.testing import CliRunner

from pn.cli.main import app
from pn.solve import SolveCache, docstore
from pn.solve import flops as fl
from pn.solve.backend import Preempted, SolveError
from pn.solve.lock import PRIORITY_DIR, file_lock, priority_waiting
from pn.solve.request import TABLE_SEATS
from pn.train import flop_library as flib
from pn.train import library_fill as lf
from pn.train import spots
from pn.train import suitmap as sm
from pn.train.engine import next_street

FAKE = Path(__file__).parent / "fixtures" / "fake_solver.py"
ORDER = TABLE_SEATS[6]


@pytest.fixture(scope="module")
def ms() -> list[flib.Matchup]:
    return flib.matchups(ORDER, 100, "6max", sb=50, bb=100)


def _m(ms: list[flib.Matchup], label_start: str) -> flib.Matchup:
    return next(m for m in ms if m.label.startswith(label_start))


# ------------------------------------------------------------------ matchups and requests


def test_matchups_cover_standard_heads_up_lines(ms: list[flib.Matchup]) -> None:
    assert len({m.mid for m in ms}) == len(ms) and len({m.label for m in ms}) == len(ms)
    assert Counter(m.pot for m in ms) == {"srp": 30, "3bp": 35, "4bp+": 15, "limped": 5}
    btn = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    assert btn.positions == ("BB", "BTN") and btn.line == "BTN raise, BB call"
    req = btn.request(("Kd", "7s", "2c"))
    assert req.source == "gto" and req.hero_cards == () and req.last_aggressor == "BTN"
    assert {(s.position, s.hero, s.line, s.vs, s.player_id) for s in req.seats} == {
        ("BTN", True, "open", None, None), ("BB", False, "call", "BTN", None)}
    put = (req.pot - 50) // 2  # the SB's 50 is dead
    assert req.pot == btn.state.pot and all(s.stack == 10_000 - put for s in req.seats)
    limp = _m(ms, "limped SB-BB")
    assert limp.request(()).last_aggressor is None
    assert next(s for s in limp.request(()).seats if s.hero).position == "BB"  # IP without raiser


def test_a_trainer_hand_on_the_line_has_the_library_matchup(ms: list[flib.Matchup]) -> None:
    btn = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    st = flib.replay(ORDER, 100, [("LJ", "fold"), ("HJ", "fold"), ("CO", "fold"),
                                  ("BTN", "raise"), ("SB", "fold"), ("BB", "call")],
                     sb=50, bb=100)  # fmt: skip
    assert st is not None
    flop = next_street(st, ("Kd", "7s", "2c"))
    assert flib.matchup_of(flop, "6max", 100).mid == btn.mid  # same key at the flop
    _, _, key = flib.flop_spot(btn, ("Kd", "7s", "2c"))
    assert key == flib.flop_spot(flib.matchup_of(flop, "6max", 100), ("Kd", "7s", "2c"))[2]
    assert flib.replay(ORDER, 100, [("LJ", "raise")], sb=50, bb=100) is None  # line broken
    three = flib.replay(ORDER, 100, [("CO", "raise"), ("BTN", "call"), ("BB", "call")],
                        sb=50, bb=100, fold_others=True)  # fmt: skip
    assert three is None  # multiway: not a library matchup


# ------------------------------------------------------------------ index and picks


def test_index_records_and_picks_only_dealable_dcfr_boards(tmp_path: Path,
                                                           ms: list[flib.Matchup]) -> None:  # fmt: skip
    m = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    cache = SolveCache(tmp_path, runner=flop_seed.texas_runner)
    index = flib.FlopIndex(tmp_path)
    keys = {}
    for b in ("Kd7s2c", "Ah8c3d"):
        _, spot, keys[b] = flib.flop_spot(m, tuple(fl.flop_cards(b)))
        cache.solve(spot, "texas")
        index.record(m, b, keys[b], 10)
    assert set(flib.FlopIndex(tmp_path).boards(m.mid)) == {"Kd7s2c", "Ah8c3d"}  # re-read
    deck = [c for r in "23456789TJQKA" for c in (r + "c", r + "d", r + "h", r + "s")]
    no_kd = [c for c in deck if c != "Kd"]
    picks = [flib.pick_board(index, cache, m.mid, no_kd, random.Random(k)) for k in range(16)]
    assert {p.doc_board for p in picks} == {("Kd", "7s", "2c"), ("Ah", "8c", "3d")}
    assert all("Kd" not in p.board for p in picks)  # Kd is in someone's hand: suits remapped
    assert flib.pick_board(index, cache, "unknown", deck, random.Random(1)) is None
    old = docstore.load(cache.path(keys["Ah8c3d"]))
    docstore.dump(cache.root, keys["Ah8c3d"], {**old, "meta": {"backend": "mwsolver"}})
    cache._mem.clear()
    no_k = [c for c in deck if c[0] != "K"]  # Kd7s2c has no image: only Ah8c3d is left
    assert flib.pick_board(index, cache, m.mid, no_k, random.Random(1)) is None  # not texas


def test_board_images_are_the_distinct_suit_isomorphic_boards() -> None:
    deck = {c for r in "23456789TJQKA" for c in (r + "c", r + "d", r + "h", r + "s")}
    for flop, n in (("Kd7s2c", 24), ("Kc9c8d", 12), ("Ks9s8s", 4), ("KcKd2h", 12)):
        imgs = flib.board_images(fl.flop_cards(flop), deck)
        assert len(imgs) == n and len({frozenset(b) for b, _ in imgs}) == n
        key = fl.canonical_key(fl.parse_flop(flop))
        assert all(fl.canonical_key(fl.parse_flop("".join(b))) == key for b, _ in imgs)
        assert all(sm.permute_cards(fl.flop_cards(flop), perm) == b for b, perm in imgs)
    assert len(flib.board_images(fl.flop_cards("Kc9c8d"), deck - {"Kh"})) == 9  # not the three Kh9h8x


def _one_board_library(tmp_path: Path, m: flib.Matchup, board: str) -> tuple:
    cache = SolveCache(tmp_path, runner=flop_seed.texas_runner)
    index = flib.FlopIndex(tmp_path)
    _, spot, key = flib.flop_spot(m, tuple(fl.flop_cards(board)))
    cache.solve(spot, "texas")
    index.record(m, board, key, 1)
    return cache, index, key


def test_a_board_clashing_with_live_cards_is_dealt_under_another_permutation(
        tmp_path: Path, ms: list[flib.Matchup]) -> None:  # fmt: skip
    m = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    cache, index, key = _one_board_library(tmp_path, m, "Kc9c8d")
    deck = [c for r in "23456789TJQKA" for c in (r + "c", r + "d", r + "h", r + "s")]
    live = [c for c in deck if c not in ("Kc", "9d", "Ah", "Kh")]  # the canonical cards clash
    picks = [flib.pick_board(index, cache, m.mid, live, random.Random(k)) for k in range(40)]
    assert all(p is not None and p.key == key and p.doc_board == ("Kc", "9c", "8d") for p in picks)
    assert all(set(p.board) <= set(live) for p in picks)
    assert all(sm.permute_cards(p.doc_board, p.perm) == p.board for p in picks)
    assert len({p.board for p in picks}) > 1  # uniform over the fitting images


def test_no_permutation_avoiding_the_live_cards_skips_the_board(tmp_path: Path,
                                                                ms: list[flib.Matchup]) -> None:  # fmt: skip
    m = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    cache, index, _ = _one_board_library(tmp_path, m, "Kc9c8d")
    deck = [c for r in "23456789TJQKA" for c in (r + "c", r + "d", r + "h", r + "s")]
    no_kings = [c for c in deck if c[0] != "K"]
    assert flib.pick_board(index, cache, m.mid, no_kings, random.Random(1)) is None
    only_ks = [c for c in deck if c not in ("Kc", "Kd", "Kh", "9s")]  # Ks but no 9s for Ks9s8x
    assert flib.pick_board(index, cache, m.mid, only_ks, random.Random(1)) is None
    pick = flib.pick_board(index, cache, m.mid, [*only_ks, "9s"], random.Random(1))
    assert pick.board[:2] == ("Ks", "9s")  # the one fitting suit pair


def test_rekey_moves_stale_matchup_ids_to_the_current_ones(tmp_path: Path,
                                                            ms: list[flib.Matchup]) -> None:  # fmt: skip
    m = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    index = flib.FlopIndex(tmp_path)
    index.record(m, "Kd7s2c", "k1", 10)  # current id
    data = index.load()
    stale = {**data["matchups"][m.mid], "boards": {"Ah8c3d": {"key": "k2", "weight": 5},
                                                   "Kd7s2c": {"key": "old", "weight": 1}}}  # fmt: skip
    data["matchups"]["0123456789abcdef"] = stale  # the same label under an old id
    data["matchups"]["fedcba9876543210"] = {**stale, "label": "not a matchup", "boards": {}}
    index.path.write_text(json.dumps(data))
    assert flib.FlopIndex(tmp_path).rekey(ms) == 1
    moved = flib.FlopIndex(tmp_path).load()["matchups"]
    assert set(moved) == {m.mid, "fedcba9876543210"}  # unknown labels are left alone
    assert moved[m.mid]["boards"] == {"Kd7s2c": {"key": "k1", "weight": 10},
                                      "Ah8c3d": {"key": "k2", "weight": 5}}  # current entry wins
    assert flib.FlopIndex(tmp_path).rekey(ms) == 0
    r = CliRunner().invoke(app, ["library", "reindex", "--table", "9"])
    assert r.exit_code == 1


# ------------------------------------------------------------------ fill planning


def test_stratified_flops_cover_buckets_and_keep_prefixes_stratified() -> None:
    n_buckets = len([k for k, v in fl.flops_by_bucket().items() if v])
    for n in (n_buckets, 50):
        fs = lf.stratified_flops(n)
        assert len(fs) == n and len({f.flop for f in fs}) == n
        assert {f.bucket for f in fs} == {k for k, v in fl.flops_by_bucket().items() if v}
        assert sum(f.weight for f in fs) == fl.N_RAW_FLOPS
    first = lf.stratified_flops(50)[:n_buckets]
    assert len({f.bucket for f in first}) == n_buckets  # round-robin over buckets
    with pytest.raises(ValueError):
        lf.stratified_flops(0)


def test_burns_and_plan_order(ms: list[flib.Matchup]) -> None:
    btn = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    co3 = _m(ms, "3bp CO-BTN 100bb: CO raise, BTN raise, CO call")
    freq = {btn.line: 0.04, co3.line: 0.02}
    rows = [{"spot": "post|3bp|ip|flop|first", "blunder_rate": 0.6},
            {"spot": "post|srp|oop|flop|first", "blunder_rate": 0.075}]  # fmt: skip
    burn = lf.burns(ms, freq, rows)
    assert burn[btn.mid] == pytest.approx(100 * 0.04 * 0.075)
    assert burn[co3.mid] == pytest.approx(100 * 0.02 * 0.6)  # rarer but more error-prone
    assert sum(b > 0 for b in burn.values()) == 2  # other matchups never reached
    flops = lf.stratified_flops(3)
    items = lf.plan(ms, burn, flops)
    # greedy burn / (1 + planned): 1.2, 0.6, 0.4 for the 3-bet pot before the SRP.s first board (0.3)
    assert [(i.matchup.mid, i.flop.flop) for i in items[:4]] == [
        (co3.mid, flops[0].flop), (co3.mid, flops[1].flop), (co3.mid, flops[2].flop),
        (btn.mid, flops[0].flop)]
    assert len(items) == 6 and len(lf.plan(ms, burn, flops, configs=1)) == 3
    prior = lf.burns(ms, {}, [])  # no library root: pot-type priors
    top = sorted(ms, key=lambda m: -prior[m.mid])[:2]
    assert {m.label for m in top} == {"srp BB-BTN 100bb: BTN raise, BB call",
                                      "srp SB-BB 100bb: SB raise, BB call"}


# ------------------------------------------------------------------ fill runs


def test_fill_is_resumable_and_reports_failures(tmp_path: Path, ms: list[flib.Matchup]) -> None:
    calls: list[str] = []

    def runner(spot, backend=None):
        calls.append(backend)
        if spot["game"]["board"][0] == "As":
            raise SolveError("dcfr exit 1: boom", 1)
        return flop_seed.texas_runner(spot, backend)

    m = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    flops = [fl.Flop("Kd7s2c", 12, "x"), fl.Flop("AsKd5h", 24, "y")]
    items = [lf.FillItem(m, f, 1.0) for f in flops]
    cache, index = SolveCache(tmp_path, runner=runner), flib.FlopIndex(tmp_path)
    out = lf.fill(items, cache, index)
    assert [r.status for r in out] == ["solved", "failed"] and "boom" in out[1].detail
    assert calls == ["texas", "texas"]
    assert index.boards(m.mid) == {"Kd7s2c": {"key": out[0].key, "weight": 12, "accuracy": 0.3}}
    again = lf.fill(items[:1], cache, index)
    assert again[0].status == "cached" and calls == ["texas", "texas"]  # nothing re-solved


def test_accuracy_passes_refine_but_never_downgrade(tmp_path: Path, ms: list[flib.Matchup]) -> None:
    calls: list[float] = []

    def runner(spot, backend=None):
        calls.append(spot["game"]["target_exploitability"])
        return flop_seed.texas_runner(spot, backend)

    m = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    item = lf.FillItem(m, fl.Flop("Kd7s2c", 12, "x"), 1.0)
    cache, index = SolveCache(tmp_path, runner=runner), flib.FlopIndex(tmp_path)
    coarse = lf.fill([item], cache, index, accuracy=1.0)
    fine = lf.fill([item], cache, index, accuracy=0.5)
    assert [r.status for r in coarse + fine] == ["solved", "solved"] and calls == [1.0, 0.5]
    assert coarse[0].key != fine[0].key  # each accuracy has its own cache key
    assert index.boards(m.mid)["Kd7s2c"] == {"key": fine[0].key, "weight": 12, "accuracy": 0.5}
    again = lf.fill([item], cache, index, accuracy=1.0) + lf.fill([item], cache, index, accuracy=0.5)
    assert [r.status for r in again] == ["finer", "finer"] and calls == [1.0, 0.5]  # equal = done
    assert index.accuracy(m.mid, "Kd7s2c") == 0.5 and index.accuracy(m.mid, "AsKd5h") is None
    assert lf.accuracy_counts(index) == {"0.5": 1}


def test_preempted_solves_wait_and_retry(tmp_path: Path, ms: list[flib.Matchup]) -> None:
    attempts: list[int] = []

    def runner(spot, backend=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise Preempted("dcfr preempted by a priority solve")
        return flop_seed.texas_runner(spot, backend)

    waits: list[int] = []
    item = lf.FillItem(_m(ms, "srp BB-BTN"), fl.Flop("Kd7s2c", 1, "x"), 1.0)
    out = lf.fill([item], SolveCache(tmp_path, runner=runner), flib.FlopIndex(tmp_path),
                  wait=lambda: waits.append(1))  # fmt: skip
    assert out[0].status == "solved" and len(attempts) == 2 and waits == [1]


def test_texas_run_is_killed_when_a_priority_solve_waits(tmp_path: Path) -> None:
    from pn.solve.texas import run_texas

    sleeper = tmp_path / "sleep.py"
    sleeper.write_text("import time; time.sleep(30)\n")
    _, spot, _ = flib.flop_spot(flib.matchups(ORDER, 100, "6max", sb=50, bb=100)[0],
                                ("Kd", "7s", "2c"))  # fmt: skip
    flag = threading.Event()
    threading.Timer(0.3, flag.set).start()
    t0 = time.monotonic()
    with pytest.raises(Preempted):
        run_texas(spot, cmd=[sys.executable, str(sleeper)], should_stop=flag.is_set)
    assert time.monotonic() - t0 < 5


def test_file_lock_lets_priority_solves_go_first(tmp_path: Path) -> None:
    order: list[str] = []
    assert not priority_waiting(tmp_path)
    with file_lock(tmp_path, priority=True):
        assert priority_waiting(tmp_path)
        t = threading.Thread(target=lambda: _ordinary(tmp_path, order))
        t.start()
        time.sleep(0.5)
        order.append("priority")
    t.join(5)
    assert order == ["priority", "ordinary"] and not priority_waiting(tmp_path)
    (tmp_path / PRIORITY_DIR / "999999999-1").touch()  # a dead process's marker
    assert not priority_waiting(tmp_path) and not list((tmp_path / PRIORITY_DIR).iterdir())


def _ordinary(root: Path, order: list[str]) -> None:
    with file_lock(root):
        order.append("ordinary")


def test_background_holder_yields_to_waiting_ordinary_solves(tmp_path: Path) -> None:
    from pn.solve.lock import WAITING_DIR, ordinary_waiting

    order: list[str] = []
    with file_lock(tmp_path, background=True):
        t = threading.Thread(target=lambda: _ordinary(tmp_path, order))
        t.start()
        time.sleep(0.5)
        assert ordinary_waiting(tmp_path)  # the ordinary waiter left its marker
        order.append("background-1")
    # the background holder must not grab the lock again while the ordinary waiter is pending
    t2 = threading.Thread(target=lambda: _background(tmp_path, order))
    t2.start()
    t.join(5)
    t2.join(5)
    assert order == ["background-1", "ordinary", "background-2"], order
    assert not ordinary_waiting(tmp_path) and not list((tmp_path / WAITING_DIR).iterdir())


def _background(root: Path, order: list[str]) -> None:
    with file_lock(root, background=True):
        order.append("background-2")


def test_single_fill_per_cache_root(tmp_path: Path) -> None:
    with lf.single_fill(tmp_path), pytest.raises(RuntimeError, match="already running"), \
            lf.single_fill(tmp_path):
        pass
    with lf.single_fill(tmp_path):  # released
        pass


def test_cli_fill_dry_run_fill_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         fake_texas: Path) -> None:  # fmt: skip
    from rich.console import Console

    from pn.cli import solve_cmds

    monkeypatch.setattr(solve_cmds, "console", Console(width=200))
    monkeypatch.setenv("PN_SOLVE_CACHE", str(tmp_path / "cache"))
    db = str(tmp_path / "none.sqlite")
    r = CliRunner().invoke(app, ["library", "fill", "--dry-run", "--configs", "2", "--flops", "2",
                                 "--db", db])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert "4 solves planned" in r.output and "srp BB-BTN" in r.output
    r = CliRunner().invoke(app, ["library", "fill", "--dry-run", "--configs", "0", "--flops", "1",
                                 "--pots", "srp", "--pots", "3bp", "--db", db])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert " srp " in r.output and " 3bp " in r.output
    assert " limped " not in r.output and " 4bp+ " not in r.output
    bad = CliRunner().invoke(app, ["library", "fill", "--backend", "mwsolver", "--db", db])
    assert bad.exit_code == 1
    bad = CliRunner().invoke(app, ["library", "fill", "--pots", "5bp", "--db", db])
    assert bad.exit_code == 1
    r = CliRunner().invoke(app, ["library", "fill", "--configs", "1", "--flops", "2", "--db", db])
    assert r.exit_code == 0, r.output
    assert r.output.count("solved ") == 2 and "2/2 boards in the library" in r.output
    index = flib.FlopIndex(tmp_path / "cache").load()["matchups"]
    assert len(index) == 1 and len(next(iter(index.values()))["boards"]) == 2
    st = CliRunner().invoke(app, ["library", "status", "--flops", "2", "--db", db])
    assert st.exit_code == 0, st.output
    assert "1/85 matchups with a solved board, 2 boards" in st.output
    assert "2/2" in st.output and "boards by accuracy: 0.3% pot: 2" in st.output
    r = CliRunner().invoke(app, ["library", "fill", "--configs", "1", "--flops", "2", "--db", db,
                                 "--accuracy", "1", "--accuracy", "0.3"])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert r.output.count("finer ") == 4 and "4/4 boards in the library" in r.output
    assert CliRunner().invoke(app, ["library", "fill", "--accuracy", "0", "--db", db]).exit_code == 1


def test_line_key_skips_folds_and_counts_allin_as_raise() -> None:
    acts = [("LJ", "fold"), ("BTN", "raise"), ("SB", "fold"), ("BB", "allin"), ("BTN", "call")]
    assert spots.line_key(acts) == "BTN raise, BB raise, BTN call"


def test_cli_fill_shard_splits_the_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from rich.console import Console

    from pn.cli import solve_cmds

    monkeypatch.setattr(solve_cmds, "console", Console(width=200))
    db = str(tmp_path / "none.sqlite")
    base = ["library", "fill", "--dry-run", "--configs", "3", "--flops", "3", "--db", db]
    whole = CliRunner().invoke(app, base)
    assert whole.exit_code == 0, whole.output
    order = [ln for ln in whole.output.splitlines() if "100bb" in ln and " sample " not in ln]
    assert len(order) == 9
    shards = [CliRunner().invoke(app, base + ["--shard", f"{i}/2"]) for i in range(2)]
    assert all(s.exit_code == 0 for s in shards), [s.output for s in shards]
    parts = [[ln for ln in s.output.splitlines() if "100bb" in ln and " sample " not in ln] for s in shards]
    assert parts[0] == order[0::2] and parts[1] == order[1::2]  # disjoint, still burn-ordered
    assert "5 solves planned (shard 0/2)" in shards[0].output
    for bad in ("2/2", "0/0", "x", "1", "-1/2"):
        assert CliRunner().invoke(app, base + ["--shard", bad]).exit_code == 1, bad


def test_all_flops_are_every_canonical_flop_heaviest_first() -> None:
    out = lf.all_flops()
    assert len(out) == 1755 and len({f.flop for f in out}) == 1755
    assert sum(f.weight for f in out) == 22100
    assert [f.weight for f in out] == sorted((f.weight for f in out), reverse=True)
    assert {f.flop for f in lf.stratified_flops(50)} <= {f.flop for f in out}  # the 50 are a subset


def test_cached_only_records_pulled_documents_without_solving(tmp_path: Path,
                                                               ms: list[flib.Matchup]) -> None:  # fmt: skip
    calls: list[str] = []

    def runner(spot, backend=None):
        calls.append(spot["game"]["board"][0])
        return flop_seed.texas_runner(spot, backend)

    m = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    flops = [fl.Flop("Kd7s2c", 12, "x"), fl.Flop("AsKd5h", 24, "y")]
    items = [lf.FillItem(m, f, 1.0) for f in flops]
    cache = SolveCache(tmp_path, runner=runner)
    lf.fill(items[:1], cache, flib.FlopIndex(tmp_path), accuracy=1.0)  # one document, as if pulled
    (tmp_path / flib.INDEX_NAME).unlink()  # a pulled document has no index entry yet
    index = flib.FlopIndex(tmp_path)
    writes: list[float] = []
    out = lf.fill(items, cache, index, accuracy=1.0, cached_only=True,
                  on_result=lambda r: writes.append((tmp_path / flib.INDEX_NAME).exists()))
    assert [r.status for r in out] == ["cached", "missing"] and calls == ["Kd"]  # nothing solved
    assert writes == [False, False]  # one index write at the end of the walk, not per board
    assert index.boards(m.mid) == {"Kd7s2c": {"key": out[0].key, "weight": 12, "accuracy": 1.0}}
    # a stratified fill recorded the chunk's weight (564); the wide plan's raw weight replaces it
    index.record(m, "Kd7s2c", out[0].key, 564, 1.0)
    again = lf.fill(items[:1], cache, flib.FlopIndex(tmp_path), accuracy=1.0, cached_only=True)
    assert again[0].status == "finer" and flib.FlopIndex(tmp_path).boards(m.mid)["Kd7s2c"]["weight"] == 12


def test_a_stale_indexed_key_is_solved_again_not_skipped(tmp_path: Path, ms: list[flib.Matchup]) -> None:
    """A board indexed at or finer than the pass under a key that is no longer the board's (the
    spot changed, e.g. new seat ranges) is not "finer": --cached-only keeps the old entry while
    the new document is missing and records it once pulled; a fill re-solves and replaces it."""
    calls: list[str] = []

    def runner(spot, backend=None):
        calls.append(spot["game"]["board"][0])
        return flop_seed.texas_runner(spot, backend)

    m = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    item = lf.FillItem(m, fl.Flop("Kd7s2c", 12, "x"), 1.0)
    cache = SolveCache(tmp_path, runner=runner)
    flib.FlopIndex(tmp_path).record(m, "Kd7s2c", "old-key", 12, 0.5)  # finer than the pass
    assert lf.indexed(flib.FlopIndex(tmp_path), m, "Kd7s2c", 1.0) is None
    out = lf.fill([item], cache, flib.FlopIndex(tmp_path), accuracy=1.0, cached_only=True)
    assert out[0].status == "missing" and calls == []
    assert flib.FlopIndex(tmp_path).boards(m.mid)["Kd7s2c"]["key"] == "old-key"  # never dropped
    out = lf.fill([item], cache, flib.FlopIndex(tmp_path), accuracy=1.0)
    assert out[0].status == "solved" and calls == ["Kd"]
    assert flib.FlopIndex(tmp_path).boards(m.mid)["Kd7s2c"] == {"key": out[0].key, "weight": 12, "accuracy": 1.0}
    assert lf.indexed(flib.FlopIndex(tmp_path), m, "Kd7s2c", 1.0) == 1.0
    assert lf.fill([item], cache, flib.FlopIndex(tmp_path), accuracy=1.0)[0].status == "finer"
    flib.FlopIndex(tmp_path).record(m, "Kd7s2c", "old-key", 12, 1.0)  # as on a machine that pulled
    out = lf.fill([item], cache, flib.FlopIndex(tmp_path), accuracy=1.0, cached_only=True)
    assert out[0].status == "cached" and calls == ["Kd"]
    assert flib.FlopIndex(tmp_path).boards(m.mid)["Kd7s2c"]["key"] == out[0].key


def test_index_batch_defers_writes_and_reads_its_own_records(tmp_path: Path,
                                                             ms: list[flib.Matchup]) -> None:  # fmt: skip
    m = _m(ms, "srp BB-BTN 100bb: BTN raise, BB call")
    index = flib.FlopIndex(tmp_path)
    with index.batch():
        index.record(m, "Kd7s2c", "k1", 12, 1.0)
        index.record(m, "AsKd5h", "k2", 24, 0.5)
        assert index.accuracy(m.mid, "Kd7s2c") == 1.0 and not (tmp_path / flib.INDEX_NAME).exists()
    assert flib.FlopIndex(tmp_path).boards(m.mid) == {
        "Kd7s2c": {"key": "k1", "weight": 12, "accuracy": 1.0},
        "AsKd5h": {"key": "k2", "weight": 24, "accuracy": 0.5}}


def test_cli_flops_all_cached_only_and_remaining(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 fake_texas: Path) -> None:  # fmt: skip
    from rich.console import Console

    from pn.cli import solve_cmds

    monkeypatch.setattr(solve_cmds, "console", Console(width=200))
    monkeypatch.setenv("PN_SOLVE_CACHE", str(tmp_path / "cache"))
    db = str(tmp_path / "none.sqlite")
    r = CliRunner().invoke(app, ["library", "fill", "--dry-run", "--configs", "1", "--flops", "all",
                                 "--accuracy", "1", "--db", db])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert "1755 solves planned, 1755 remaining at 1% pot" in r.output
    r = CliRunner().invoke(app, ["library", "fill", "--configs", "1", "--flops", "2", "--accuracy", "1",
                                 "--db", db])  # fmt: skip
    assert r.exit_code == 0, r.output and r.output.count("solved ") == 2
    (tmp_path / "cache" / flib.INDEX_NAME).unlink()  # as after a pull: documents, no entries
    r = CliRunner().invoke(app, ["library", "fill", "--configs", "1", "--flops", "all", "--accuracy", "1",
                                 "--cached-only", "--db", db])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert r.output.count("cached ") == 2 and "missing" not in r.output
    assert "2/1755 boards in the library" in r.output and fake_texas.read_text().count("texas") == 2
    r = CliRunner().invoke(app, ["library", "fill", "--dry-run", "--configs", "1", "--flops", "all",
                                 "--accuracy", "1", "--db", db])  # fmt: skip
    assert "1755 solves planned, 1753 remaining at 1% pot" in r.output
    st = CliRunner().invoke(app, ["library", "status", "--flops", "all", "--db", db])
    assert st.exit_code == 0 and "2/1755" in st.output
    assert CliRunner().invoke(app, ["library", "fill", "--flops", "most", "--db", db]).exit_code == 1


def test_cli_fill_refuses_to_plan_without_the_gto_wizard_sizes(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    # The plan hashes the preflop raise sizes, which come from data/gtowizard; a machine without
    # the file would silently plan default sizes and solve boards no other machine can use.
    from pn.profile import gtowizard
    from pn.train import preflop as pre

    monkeypatch.setattr(gtowizard, "freqs_path", lambda: tmp_path / "missing_freqs.json")
    pre._freqs.cache_clear()  # the loader is cached per process
    monkeypatch.setenv("PN_SOLVE_CACHE", str(tmp_path / "cache"))
    r = CliRunner().invoke(app, ["library", "fill", "--dry-run", "--configs", "1", "--flops", "1",
                                 "--db", str(tmp_path / "none.sqlite")])  # fmt: skip
    assert r.exit_code == 1 and "GTO Wizard" in r.output
    pre._freqs.cache_clear()  # leave later tests the real file


def test_cli_fill_reverse_walks_the_same_order_from_the_light_end(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    # Two shard sets on one machine pool: one forward, one `--reverse`, meet in the middle with no overlap.
    monkeypatch.setenv("PN_SOLVE_CACHE", str(tmp_path / "cache"))
    db = str(tmp_path / "none.sqlite")
    args = ["library", "fill", "--dry-run", "--configs", "2", "--flops", "3", "--db", db]
    def order(out: str) -> list[str]:  # the planned items, not the per-library sample line
        return [ln for ln in out.splitlines() if (" srp " in ln or " 3bp " in ln) and " sample " not in ln]

    fwd = order(CliRunner().invoke(app, args).output)
    rev = order(CliRunner().invoke(app, [*args, "--reverse"]).output)
    assert len(fwd) == 6 and rev == fwd[::-1]
    r = CliRunner().invoke(app, [*args, "--reverse", "--shard", "0/2"])
    assert r.exit_code == 0 and "3 solves planned (shard 0/2)" in r.output
