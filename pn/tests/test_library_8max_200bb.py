"""Plan 16 P3/P4: the weighted 184-flop subset and the fill tooling for the 8-max 100bb, 6-max 200bb
and 8-max 200bb libraries (pn.train.library_fill, `pn library fill --lib/--subset/--new-only`).
Every key the fill plans must be the key the trainer looks up, and the 6-max 100bb library's 85
matchup ids and flop keys must not move."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import flop_seed
import pytest
from typer.testing import CliRunner

from pn.cli.main import app
from pn.profile import charts
from pn.solve import SolveCache
from pn.solve import flops as fl
from pn.train import flop_library as flib
from pn.train import library_fill as lf
from pn.train import preflop as pre
from pn.train import textures as tx

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "hu_flop_6max_100bb_keys.json").read_text())
SUBSET_DIGEST = "4454a91e7cdec512"  # sha256 of "flop:weight,..." of subset_flops(184): a change strands indexed boards
EP = {"UTG", "UTG1"}
NEW_LIBS = ((8, 100), (6, 200), (8, 200))


@pytest.fixture(scope="module")
def libs() -> dict[tuple[int, int], list[flib.Matchup]]:
    if not (charts.charts_dir() / charts.PC_FILE).exists() or not pre.has_freqs():
        pytest.skip("PokerCoaching charts or GTO Wizard frequencies not present")
    return {(t, d): lf.lib_matchups(t, d) for t, d in ((6, 100), *NEW_LIBS)}


@pytest.fixture(scope="module")
def kept(libs) -> dict[tuple[int, int], tuple[list[flib.Matchup], int, int]]:
    return {(t, d): lf.new_lines(t, d, libs[(t, d)]) for t, d in NEW_LIBS}


def _cards(board: str) -> tuple[str, ...]:
    return tuple(board[i : i + 2] for i in (0, 2, 4))


def _ep_acts(m: flib.Matchup) -> bool:
    return any(m.state.positions[a.seat] in EP for a in m.state.history
               if a.street == "preflop" and a.kind != "fold")  # fmt: skip


def _share(flops: list[fl.Flop], key) -> Counter:
    out: Counter = Counter()
    for f in flops:
        out[key(f.flop)] += f.weight
    return out


# ------------------------------------------------------------------ P3: the 184-flop subset


def test_subset_is_184_distinct_canonical_flops_carrying_every_raw_flop() -> None:
    sub = lf.subset_flops(184)
    canon = {f.flop for f in fl.canonical_flops()}
    assert len(sub) == 184 and len({f.flop for f in sub}) == 184 and {f.flop for f in sub} <= canon
    assert sum(f.weight for f in sub) == fl.N_RAW_FLOPS
    assert all(f.bucket == fl.bucket_of(f.flop) for f in sub)


def test_subset_is_deterministic() -> None:
    a, b = lf.subset_flops(184), lf.subset_flops(184)
    assert a == b
    blob = ",".join(f"{f.flop}:{f.weight}" for f in a)
    assert hashlib.sha256(blob.encode()).hexdigest()[:16] == SUBSET_DIGEST


def test_subset_weights_reproduce_the_texture_and_high_card_distributions() -> None:
    sub, full = lf.subset_flops(184), list(fl.canonical_flops())
    for key, tol in ((tx.texture, 1.0), (lambda b: b[0], 1.0),  # stratified: exact
                     (lambda b: len({c[1] for c in fl.parse_flop(b)}), 1.0),  # suit pattern
                     (lambda b: fl.classify(fl.parse_flop(b)).connectivity >= 2, 1.0)):  # fmt: skip
        s, f = _share(sub, key), _share(full, key)
        err = max(abs(s[k] - f[k]) * 100 / fl.N_RAW_FLOPS for k in s | f)
        assert err < tol, (key, err)
    tex = _share(sub, tx.texture)
    assert {t: tex[t] / fl.N_RAW_FLOPS for t in tx.TEXTURES} == pytest.approx(tx.FLOP_SHARE, abs=1e-12)
    assert _share(sub, lambda b: b[0]) == _share(full, lambda b: b[0])
    assert {tx.texture(f.flop) for f in sub[:8]} >= {"Ace", "King", "Queen"}  # round-robin over strata


def test_subset_size_bounds() -> None:
    assert len(lf.subset_flops(35)) == 35  # one per texture x high card stratum
    assert len(lf.subset_flops(fl.N_CANONICAL_FLOPS)) == fl.N_CANONICAL_FLOPS
    for bad in (34, 0, fl.N_CANONICAL_FLOPS + 1):
        with pytest.raises(ValueError):
            lf.subset_flops(bad)


# ------------------------------------------------------------------ P4: libraries and keys


def test_parse_lib() -> None:
    assert lf.parse_lib("8x200") == (8, 200) and lf.parse_lib(" 6x100 ") == (6, 100)
    for bad in ("8x150", "7x100", "8-200", "x100", ""):
        with pytest.raises(ValueError):
            lf.parse_lib(bad)


def test_6max_100bb_ids_and_flop_keys_do_not_move(libs) -> None:
    ms = libs[(6, 100)]
    assert sorted(m.mid for m in ms) == sorted(GOLDEN)
    for m in ms:
        g = GOLDEN[m.mid]
        assert m.line == g["line"]
        assert flib.flop_spot(m, _cards(g["board"]), g["accuracy"])[2] == g["key"], m.label
        assert lf.trainer_key(m, _cards(g["board"]), g["accuracy"]) == (m.mid, g["key"]), m.label


def test_new_only_skips_aliased_and_limped_lines(libs, kept) -> None:
    expect = {(8, 100): (88, 85, 2), (6, 200): (80, 0, 5), (8, 200): (88, 85, 2)}
    alias8: set[str] = set()
    for (t, d), (n_keep, n_alias, n_limp) in expect.items():
        ms = libs[(t, d)]
        keep, a, lim = kept[(t, d)]
        assert (len(keep), a, lim) == (n_keep, n_alias, n_limp), (t, d)
        assert all(m.pot != "limped" for m in keep)
        assert len({m.mid for m in keep}) == len(keep)
        if t == 8:  # aliased = exactly the lines where UTG/UTG1 never act (Plan 16 P0/P1)
            assert all(_ep_acts(m) for m in keep)
            alias = lf.aliased(ms, libs[(6, d)])
            assert alias == {m.mid for m in ms if not _ep_acts(m)}
            alias8 |= alias
    assert sum(len(kept[lib][0]) for lib in NEW_LIBS) == 256
    mids = [m.mid for t, d in ((6, 100), *NEW_LIBS) for m in libs[(t, d)]]
    assert len(set(mids)) == len(mids) and not alias8 & set(GOLDEN)  # ids differ per table/depth


def test_planned_keys_are_the_trainer_lookups(kept) -> None:
    board = _cards(lf.subset_flops(184)[0].flop)
    for lib in NEW_LIBS:
        for m in kept[lib][0][::9]:
            assert lf.trainer_key(m, board, 1.0) == (m.mid, flib.flop_spot(m, board, 1.0)[2]), m.label


def test_index_records_table_and_subset_and_rekey_keeps_tables_apart(tmp_path: Path, libs) -> None:
    six = next(m for m in libs[(6, 200)] if m.label.startswith("srp BB-BTN"))
    eight = next(m for m in libs[(8, 200)] if m.label == six.label)  # same label, other table
    assert eight.mid != six.mid
    cache = SolveCache(tmp_path, runner=flop_seed.texas_runner)
    items = [lf.FillItem(six, f, 1.0) for f in lf.subset_flops(184)[:2]]
    out = lf.fill(items, cache, flib.FlopIndex(tmp_path), accuracy=1.0, subset=184)
    assert [r.status for r in out] == ["solved", "solved"]
    entry = flib.FlopIndex(tmp_path).load()["matchups"][six.mid]
    assert entry["subset"] == 184 and entry["table"] == "6max" and entry["depth"] == 200
    assert {b: e["weight"] for b, e in entry["boards"].items()} == {f.flop: f.weight for f in lf.subset_flops(184)[:2]}
    # an 8-max reindex must not grab 6-max entries of the same label
    assert flib.FlopIndex(tmp_path).rekey(libs[(8, 200)]) == 0
    assert set(flib.FlopIndex(tmp_path).load()["matchups"]) == {six.mid}


def test_full_fill_marks_the_entry_full_and_a_subset_rerun_keeps_it(tmp_path: Path, libs) -> None:
    """Plan 17 C3: a `--flops all` fill records the matchup's intent (no `subset`, flops "all"), so
    `pn library status` counts it extending until all 1,755 boards are indexed, then full; a later
    subset pass over the same matchup never downgrades it."""
    from pn.train import library_alias as la

    m = next(x for x in libs[(6, 200)] if x.label.startswith("srp BB-BTN"))
    cache = SolveCache(tmp_path, runner=flop_seed.texas_runner)
    sub = lf.subset_flops(184)[:1]
    lf.fill([lf.FillItem(m, f, 1.0) for f in sub], cache, flib.FlopIndex(tmp_path), accuracy=1.0, subset=184)
    assert flib.FlopIndex(tmp_path).load()["matchups"][m.mid]["subset"] == 184
    rest = [f for f in lf.all_flops() if f.flop not in {s.flop for s in sub}][:1]
    lf.fill([lf.FillItem(m, f, 1.0) for f in rest], cache, flib.FlopIndex(tmp_path), accuracy=1.0, full=True)
    entry = flib.FlopIndex(tmp_path).load()["matchups"][m.mid]
    assert "subset" not in entry and entry["flops"] == "all" and len(entry["boards"]) == 2
    more = lf.subset_flops(184)[1:2]  # a new board, so the subset pass records it
    lf.fill([lf.FillItem(m, f, 1.0) for f in more], cache, flib.FlopIndex(tmp_path), accuracy=1.0, subset=184)
    entry = flib.FlopIndex(tmp_path).load()["matchups"][m.mid]
    assert "subset" not in entry and len(entry["boards"]) == 3
    c = la.coverage(flib.FlopIndex(tmp_path), 6, 200)
    # 3 of 1,755 boards: the extension has started, it is not full (review: status said full from day one)
    assert (c["full"], c["extending"], c["subset"]) == (0, 1, 0)
    row = next(r for r in c["rows"] if r["mid"] == m.mid)
    assert row["fill"] == "extending" and row["boards"] == 3
    assert "1 extending" in la.summary_line(c)
    index = flib.FlopIndex(tmp_path)
    with index.batch():  # every canonical flop indexed: now it is full
        for f in lf.all_flops():
            index.record(m, f.flop, "0" * 64, f.weight, 1.0, full=True)
    c = la.coverage(flib.FlopIndex(tmp_path), 6, 200)
    assert (c["full"], c["extending"]) == (1, 0) and len(lf.all_flops()) == la.N_FLOPS == 1755


def test_cached_only_weight_reset_does_not_mark_a_subset_entry_full(tmp_path: Path, libs) -> None:
    """The first `--cached-only` walk with `--flops all` only resets the 184 subset boards' chunk
    weights to their raw weights: that is not an extension, so the entry keeps its `subset`."""
    from dataclasses import replace

    from pn.train import library_alias as la

    m = next(x for x in libs[(6, 200)] if x.label.startswith("srp BB-BTN"))
    cache = SolveCache(tmp_path, runner=flop_seed.texas_runner)
    sub = lf.subset_flops(184)[:2]
    chunked = [replace(f, weight=f.weight + 7) for f in sub]  # a stratified pass's chunk weight
    lf.fill([lf.FillItem(m, f, 1.0) for f in chunked], cache, flib.FlopIndex(tmp_path), accuracy=1.0, subset=184)
    out = lf.fill([lf.FillItem(m, f, 1.0) for f in sub], cache, flib.FlopIndex(tmp_path), accuracy=1.0,
                  cached_only=True, full=True)  # fmt: skip
    assert [r.status for r in out] == ["finer", "finer"]
    entry = flib.FlopIndex(tmp_path).load()["matchups"][m.mid]
    assert {b: e["weight"] for b, e in entry["boards"].items()} == {f.flop: f.weight for f in sub}
    assert entry["subset"] == 184 and "flops" not in entry
    assert la.coverage(flib.FlopIndex(tmp_path), 6, 200)["subset"] == 1


def test_missing_inputs_names_each_absent_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(charts, "charts_dir", lambda: tmp_path)
    (tmp_path / charts.PC_FILE).write_text("")
    (tmp_path / "common.txt").write_text("")
    missing = lf.missing_inputs([(8, 200)])
    assert "chart file config/preflop_charts/8max.txt" in missing
    assert "chart file config/preflop_charts/gtowizard_200bb.txt" in missing
    assert not any(charts.PC_FILE in x or "common.txt" in x for x in missing)
    monkeypatch.setattr(pre, "_freqs", lambda: {"100|x": {}})
    assert "GTO Wizard 200bb frequencies" in lf.missing_inputs([(6, 200)])


# ------------------------------------------------------------------ CLI


def _run(args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from rich.console import Console

    from pn.cli import solve_cmds

    monkeypatch.setattr(solve_cmds, "console", Console(width=300))
    monkeypatch.setenv("PN_SOLVE_CACHE", str(tmp_path / "cache"))
    return CliRunner().invoke(app, ["library", "fill", *args, "--db", str(tmp_path / "none.sqlite")])


def test_cli_dry_run_counts_and_compares_a_sample_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      kept) -> None:  # fmt: skip
    # --configs 3: the counts cover every matchup, the order (and the sample) the top three
    r = _run(["--dry-run", "--configs", "3", "--subset", "184", "--lib", "8x100", "--lib", "6x200",
              "--new-only", "--accuracy", "1"], tmp_path, monkeypatch)  # fmt: skip
    assert r.exit_code == 0, r.output
    assert "8max/100: 88 of 175 matchups (85 aliased, 2 limped skipped) x 184 boards" in r.output
    assert "6max/200: 80 of 85 matchups (0 aliased, 5 limped skipped) x 184 boards" in r.output
    assert "trainer lookup same" in r.output and "DIFFERENT" not in r.output
    assert f"{3 * 184} solves planned, {3 * 184} remaining at 1% pot" in r.output
    by_mid = {m.mid: m for lib in NEW_LIBS for m in kept[lib][0]}
    first = min(f.flop for f in lf.subset_flops(184))
    for ln in (x for x in r.output.splitlines() if " sample " in x):
        mid, key = ln.split(" mid ")[1].split(" key ")
        m = by_mid[mid]
        assert f"{m.label} {first}:" in ln and key.split(";")[0] == flib.flop_spot(m, _cards(first), 1.0)[2]
    shard = _run(["--dry-run", "--configs", "2", "--subset", "184", "--lib", "8x200", "--new-only",
                  "--shard", "1/7"], tmp_path, monkeypatch)  # fmt: skip
    assert shard.exit_code == 0 and f"{len(range(1, 2 * 184, 7))} solves planned (shard 1/7)" in shard.output


def test_cli_refuses_bad_or_conflicting_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for args in (["--subset", "184", "--flops", "10"], ["--subset", "3"], ["--lib", "8x150"],
                 ["--lib", "9x100"], ["--depth", "150"]):  # fmt: skip
        r = _run(["--dry-run", *args], tmp_path, monkeypatch)
        assert r.exit_code == 1, (args, r.output)


def test_cli_fails_fast_when_a_chart_file_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lf, "missing_inputs", lambda libs: ["chart file config/preflop_charts/8max.txt"])
    r = _run(["--dry-run", "--lib", "8x100", "--subset", "184"], tmp_path, monkeypatch)
    assert r.exit_code == 1 and "missing inputs" in r.output and "8max.txt" in r.output


# ------------------------------------------------------------------ per-depth accuracy (200bb at 2%)


def test_parse_depth_accuracy() -> None:
    assert lf.parse_depth_accuracy(["200=2", "100=1"]) == {200: 2.0, 100: 1.0}
    assert lf.parse_depth_accuracy([]) == {}
    for bad in ("150=2", "200", "200=0", "x=2", "200=y"):
        with pytest.raises(ValueError):
            lf.parse_depth_accuracy([bad])


def test_200bb_boards_solve_to_their_depth_accuracy_and_the_trainer_finds_them(tmp_path: Path, kept) -> None:
    import random

    deep = next(m for m in kept[(6, 200)][0] if m.label.startswith("4bp+"))
    ep = next(m for m in kept[(8, 100)][0] if m.pot == "4bp+")
    flop = lf.subset_flops(184)[0]
    board = _cards(flop.flop)
    cache = SolveCache(tmp_path, runner=flop_seed.texas_runner)
    items = [lf.FillItem(deep, flop, 1.0), lf.FillItem(ep, flop, 1.0)]
    out = lf.fill(items, cache, flib.FlopIndex(tmp_path), accuracy=1.0, subset=184,
                  depth_accuracy={200: 2.0})  # fmt: skip
    assert [r.status for r in out] == ["solved", "solved"]
    assert out[0].key == flib.flop_spot(deep, board, 2.0)[2] != flib.flop_spot(deep, board, 1.0)[2]
    assert out[1].key == flib.flop_spot(ep, board, 1.0)[2]
    index = flib.FlopIndex(tmp_path)
    for m, acc, res in ((deep, 2.0, out[0]), (ep, 1.0, out[1])):
        mid, key = lf.trainer_key(m, board, acc)  # the trainer's own hand on the line
        assert (mid, key) == (m.mid, res.key)
        assert index.boards(mid)[flop.flop] == {"key": key, "weight": flop.weight, "accuracy": acc}
        deck = [r + s for r in "23456789TJQKA" for s in "cdhs"]
        pick = flib.pick_board(index, cache, mid, deck, random.Random(1))
        assert pick is not None and pick.key == key and sorted(pick.doc_board) == sorted(board)
    again = lf.fill(items[:1], cache, index, accuracy=1.0, depth_accuracy={200: 2.0})
    assert again[0].status == "finer"  # recorded at 2%, the target of that depth


def test_cli_depth_accuracy_sets_the_200bb_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kept) -> None:
    r = _run(["--dry-run", "--configs", "2", "--subset", "184", "--lib", "6x200", "--lib", "8x100",
              "--new-only", "--accuracy", "1", "--depth-accuracy", "200=2"], tmp_path, monkeypatch)  # fmt: skip
    assert r.exit_code == 0, r.output
    assert "6max/200: 80 of 85 matchups (0 aliased, 5 limped skipped) x 184 boards at 2% pot" in r.output
    assert "8max/100: 88 of 175 matchups (85 aliased, 2 limped skipped) x 184 boards at 1% pot" in r.output
    assert "DIFFERENT" not in r.output and "368 solves planned, 368 remaining" in r.output
    by_mid = {m.mid: m for lib in NEW_LIBS for m in kept[lib][0]}
    first = min(f.flop for f in lf.subset_flops(184))
    for ln in (x for x in r.output.splitlines() if " sample " in x):
        mid, key = ln.split(" mid ")[1].split(" key ")
        m = by_mid[mid]
        assert key.split(";")[0] == flib.flop_spot(m, _cards(first), 2.0 if m.depth == 200 else 1.0)[2]
    assert _run(["--dry-run", "--depth-accuracy", "150=2"], tmp_path, monkeypatch).exit_code == 1


# ------------------------------------------------------------------ keys review (2026-10-15)


def test_subset_reproduces_pairedness_low_card_suits_and_connectedness() -> None:
    """Trips were 3.5% of the subset (0.24% of flops) when a light trips flop became a chunk's
    weighted median; the subset is the only board set of every new library."""
    sub, full = lf.subset_flops(184), list(fl.canonical_flops())

    def ranks(b: str) -> list[int]:
        return [c[0] for c in fl.parse_flop(b)]

    for name, key, tol in (("pairedness", lambda b: 3 - len(set(ranks(b))), 0.5),
                           ("low card", lambda b: min(ranks(b)), 1.0),
                           ("suit pattern", lambda b: len({c[1] for c in fl.parse_flop(b)}), 1.0),
                           ("connected", lambda b: fl.classify(fl.parse_flop(b)).connectivity >= 2, 1.0)):  # fmt: skip
        s, f = _share(sub, key), _share(full, key)
        err = max(abs(s[k] - f[k]) * 100 / fl.N_RAW_FLOPS for k in s | f)
        assert err < tol, (name, err)
    trips = sum(f.weight for f in sub if len(set(ranks(f.flop))) == 1)
    assert trips * 100 / fl.N_RAW_FLOPS < 0.5
    assert all(isinstance(f.weight, int) and f.weight >= 1 for f in sub)


def test_fill_and_walk_skip_aliased_matchups_without_solving(tmp_path: Path, libs) -> None:
    """A fill or --cached-only walk without --new-only reaches alias entries: it must report
    them "aliased" (their boards are the 6-max target's), not raise after a solve."""
    line = "BTN raise, BB call"
    six = next(m for m in libs[(6, 100)] if m.line == line)
    eight = next(m for m in libs[(8, 100)] if m.line == line)
    sub = lf.subset_flops(184)
    index = flib.FlopIndex(tmp_path)
    index.record(six, sub[0].flop, "k", sub[0].weight + 1, 1.0)  # raw weight != the chunk weight
    index.alias(eight, six.mid)

    def no_solve(spot, backend=None):
        raise AssertionError("an aliased matchup was solved")

    cache = SolveCache(tmp_path, runner=no_solve)
    items = [lf.FillItem(eight, f, 1.0) for f in sub[:3]]
    for cached_only in (True, False):
        out = lf.fill(items, cache, flib.FlopIndex(tmp_path), accuracy=1.0, subset=184,
                      cached_only=cached_only)  # fmt: skip
        assert [r.status for r in out] == ["aliased"] * 3
    fresh = flib.FlopIndex(tmp_path)
    assert fresh.alias_of(eight.mid) == six.mid and fresh.boards(six.mid)[sub[0].flop]["key"] == "k"
