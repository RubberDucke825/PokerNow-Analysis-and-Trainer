"""Plan 16 P2: flop index aliases (pn.train.flop_library.FlopIndex) and `pn library alias` /
`status` (pn.train.library_alias): an 8-max line without UTG/UTG1 action reads the 6-max boards
of the same line, after an end-to-end key check; idempotent; one refusal writes nothing."""

from __future__ import annotations

import json
import random
from pathlib import Path

import flop_seed
import pytest
from typer.testing import CliRunner

from pn.cli.main import app
from pn.solve import SolveCache
from pn.solve import flops as fl
from pn.train import flop_library as flib
from pn.train import library_alias as la
from pn.train import library_fill as lf

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "hu_flop_6max_100bb_keys.json").read_text()
)


@pytest.fixture(scope="module")
def m6() -> list[flib.Matchup]:
    return la.lib_matchups(6, 100)


@pytest.fixture(scope="module")
def m8() -> list[flib.Matchup]:
    return la.lib_matchups(8, 100)


@pytest.fixture
def root(flop_template: Path, tmp_path: Path) -> Path:
    """A cache root with the fake 6-max 100bb library (4 boards per matchup)."""
    flop_seed.copy_into(flop_template, tmp_path / "cache")
    return tmp_path / "cache"


def _by_line(ms: list[flib.Matchup], line: str) -> flib.Matchup:
    return next(m for m in ms if m.line == line)


# ------------------------------------------------------------------ the index


def test_an_alias_reads_the_target_boards(root: Path, m6, m8) -> None:
    line = "BTN raise, BB call"
    six, eight = _by_line(m6, line), _by_line(m8, line)
    index = flib.FlopIndex(root)
    assert index.boards(eight.mid) == {} and index.alias_of(eight.mid) is None
    assert index.alias(eight, six.mid) is True
    assert index.alias(eight, six.mid) is False  # idempotent
    fresh = flib.FlopIndex(root)
    assert fresh.alias_of(eight.mid) == six.mid
    assert fresh.boards(eight.mid) == fresh.boards(six.mid) and len(fresh.boards(six.mid)) == 4
    board = next(iter(fresh.boards(six.mid)))
    assert fresh.accuracy(eight.mid, board) == fresh.accuracy(six.mid, board)
    deck = [r + s for r in "23456789TJQKA" for s in "shdc"]
    pick = flib.pick_board(fresh, SolveCache(root), eight.mid, deck, random.Random(1))
    assert pick is not None and pick.key in {e["key"] for e in fresh.boards(six.mid).values()}
    with pytest.raises(ValueError, match="alias"):
        fresh.record(eight, "Kd7s2c", "k", 1)  # a fill never writes under an alias
    other = _by_line(m8, "CO raise, BTN call")
    with pytest.raises(ValueError):
        fresh.alias(other, eight.mid)  # no alias of an alias
    with pytest.raises(ValueError):
        fresh.alias(six, _by_line(m6, "CO raise, BTN call").mid)  # six has boards of its own
    assert sum(lf.accuracy_counts(fresh).values()) == 4 * len(m6)  # alias entries hold none


def test_rekey_moves_an_alias_with_its_label(tmp_path: Path, m6, m8) -> None:
    six, eight = _by_line(m6, "BTN raise, BB call"), _by_line(m8, "BTN raise, BB call")
    index = flib.FlopIndex(tmp_path)
    index.record(six, "Kd7s2c", "k1", 1)
    index.alias(eight, six.mid)
    data = index.load()
    data["matchups"]["0123456789abcdef"] = data["matchups"].pop(eight.mid)  # an old id
    index.path.write_text(json.dumps(data))
    assert flib.FlopIndex(tmp_path).rekey(m8) == 1
    assert flib.FlopIndex(tmp_path).boards(eight.mid) == {"Kd7s2c": {"key": "k1", "weight": 1}}


# ------------------------------------------------------------------ candidates and keys


def test_candidates_are_the_85_non_ep_lines_at_both_depths(m6) -> None:
    for depth in (100, 200):
        pairs = la.candidates(8, depth)
        six = la.lib_matchups(6, depth)
        assert len(pairs) == 85 and all(t is not None for _, t in pairs)
        assert {t.mid for _, t in pairs} == {m.mid for m in six}  # one-to-one onto 6-max
        assert not any(la.ep_acts(m) for m, _ in pairs)
        assert sum(la.ep_acts(m) for m in la.lib_matchups(8, depth)) == 90
    assert {t.mid for _, t in la.candidates(8, 100)} == set(GOLDEN)  # ids of the real index
    with pytest.raises(ValueError):
        la.candidates(6, 100)


def test_aliased_8max_keys_equal_the_recorded_6max_keys_of_the_real_index() -> None:
    """KEY SAFETY end to end: an aliased 8-max line computes, for a real indexed board at its
    accuracy, exactly the key recorded for the 6-max document (golden list, pre-Plan 16)."""
    for m, t in la.candidates(8, 100):
        g = GOLDEN[t.mid]
        assert g["line"] == m.line
        assert flib.flop_spot(m, tuple(fl.flop_cards(g["board"])), g["accuracy"])[2] == g["key"]


# ------------------------------------------------------------------ run


def test_run_writes_85_aliases_once_and_the_trainer_lookup_reads_6max_boards(root: Path) -> None:
    index = flib.FlopIndex(root)
    rows = la.run(index, 8, 100, sample=1)
    assert [r.status for r in rows] == ["new"] * 85
    text = index.path.read_text()
    again = la.run(flib.FlopIndex(root), 8, 100, sample=1)
    assert [r.status for r in again] == ["present"] * 85 and index.path.read_text() == text
    fresh = flib.FlopIndex(root)
    for r in rows:
        boards = fresh.boards(r.matchup.mid)
        assert len(boards) == 4 and boards == fresh.boards(r.target)
        b, e = max(boards.items())  # the 8-max hand's own key finds the 6-max document
        assert flib.flop_spot(r.matchup, tuple(fl.flop_cards(b)), e.get("accuracy"))[2] == e["key"]
    assert sum("alias_of" in e for e in fresh.load()["matchups"].values()) == 85


def test_one_key_mismatch_refuses_and_writes_nothing(root: Path, m6) -> None:
    index = flib.FlopIndex(root)
    six = _by_line(m6, "CO raise, BTN call")
    data = index.load()
    board = min(data["matchups"][six.mid]["boards"])  # the board sample=1 checks
    data["matchups"][six.mid]["boards"][board]["key"] = "0" * 64  # a document of another spot
    index.path.write_text(json.dumps(data))
    text = index.path.read_text()
    rows = la.run(flib.FlopIndex(root), 8, 100, sample=1)
    bad = [r for r in rows if r.status == "refused"]
    assert len(bad) == 1 and bad[0].target == six.mid and "differs" in bad[0].detail
    assert index.path.read_text() == text


def test_a_matchup_solved_on_its_own_is_left_alone(root: Path, m8) -> None:
    eight = _by_line(m8, "BTN raise, BB call")
    flib.FlopIndex(root).record(eight, "Kd7s2c", "own", 1)
    rows = la.run(flib.FlopIndex(root), 8, 100, sample=1)
    assert next(r for r in rows if r.matchup.mid == eight.mid).status == "solved"
    fresh = flib.FlopIndex(root)
    assert fresh.alias_of(eight.mid) is None
    assert (
        set(fresh.boards(eight.mid)) == {"Kd7s2c"}
        and fresh.boards(eight.mid)["Kd7s2c"]["key"] == "own"
    )


def test_coverage_counts_solved_aliased_missing_and_skipped_limps(root: Path) -> None:
    index = flib.FlopIndex(root)
    c6 = la.coverage(index, 6, 100)
    assert (c6["solved"], c6["full"], c6["aliased"], c6["missing"]) == (85, 85, 0, 0)
    before = la.coverage(index, 8, 100)
    assert (before["aliased"], before["missing"], before["skipped"]) == (0, 175, 7)
    la.run(index, 8, 100, sample=1)
    c8 = la.coverage(flib.FlopIndex(root), 8, 100)
    assert (
        c8["solved"],
        c8["aliased"],
        c8["aliased_with_boards"],
        c8["missing"],
        c8["skipped"],
    ) == (0, 85, 85, 90, 2)  # UTG / UTG1 limps: limped pots are not solved (budget decision)
    c200 = la.coverage(index, 6, 200)
    assert (c200["missing"], c200["skipped"]) == (85, 5)
    la.run(index, 8, 200, sample=1)  # the 5 limped non-EP lines alias 6x200 limped: never solved
    c8d = la.coverage(flib.FlopIndex(root), 8, 200)
    assert (c8d["aliased"], c8d["missing"], c8d["skipped"]) == (85, 90, 7)
    rows = [r for r in c8d["rows"] if r["skipped"]]
    assert sorted(r["state"] for r in rows) == ["aliased"] * 5 + ["missing"] * 2
    assert all(r["pot"] == "limped" for r in rows)


# ------------------------------------------------------------------ CLI


def test_cli_alias_and_status(root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from rich.console import Console

    from pn.cli import solve_cmds

    monkeypatch.setattr(solve_cmds, "console", Console(width=220))
    monkeypatch.setenv("PN_SOLVE_CACHE", str(root))
    dry = CliRunner().invoke(app, ["library", "alias", "--table", "8", "--depth", "100", "--dry-run",
                                   "--sample", "1"])  # fmt: skip
    assert dry.exit_code == 0, dry.output
    assert (
        "would write 85" in dry.output
        and flib.FlopIndex(root).alias_of(la.candidates(8, 100)[0][0].mid) is None
    )
    r = CliRunner().invoke(
        app, ["library", "alias", "--table", "8", "--depth", "100", "--sample", "0"]
    )
    assert r.exit_code == 0, r.output
    assert "wrote 85, 0 present, 0 solved on their own" in r.output
    r = CliRunner().invoke(
        app, ["library", "alias", "--table", "8", "--depth", "100", "--sample", "1"]
    )
    assert "wrote 0, 85 present" in r.output
    r = CliRunner().invoke(app, ["library", "alias", "--table", "8", "--depth", "200"])
    assert r.exit_code == 0 and "wrote 85" in r.output  # the 6-max 200bb boards arrive later
    assert CliRunner().invoke(app, ["library", "alias", "--table", "6"]).exit_code == 1
    assert CliRunner().invoke(app, ["library", "alias", "--depth", "150"]).exit_code == 1
    st = CliRunner().invoke(app, ["library", "status", "--table", "8", "--flops", "4",
                                  "--db", str(tmp_path / "none.sqlite")])  # fmt: skip
    assert st.exit_code == 0, st.output
    assert (
        "8max/100bb: 175 matchups, 0 solved (0 subset, 0 extending, 0 full), 85 aliased (85 with boards), "
        "90 missing; 2 limped skipped on purpose"
    ) in st.output
    assert (
        "8max/200bb: 175 matchups, 0 solved (0 subset, 0 extending, 0 full), 85 aliased (0 with boards)"
        in st.output
    )
    assert "6max/100bb: 85 matchups, 85 solved (0 subset, 0 extending, 85 full)" in st.output
    assert " aliased  srp BB-BTN" in st.output and " skipped  limped" in st.output


# ------------------------------------------------------------------ keys review (2026-10-15)


def test_rekey_points_aliases_at_the_moved_target(tmp_path: Path, m6, m8) -> None:
    """A 6-max reindex moves the target boards to a new mid: every alias_of must follow, or the
    8-max lines deal no flop."""
    six, eight = _by_line(m6, "BTN raise, BB call"), _by_line(m8, "BTN raise, BB call")
    index = flib.FlopIndex(tmp_path)
    index.record(six, "Kd7s2c", "k1", 1)
    index.alias(eight, six.mid)
    data = index.load()
    data["matchups"]["0123456789abcdef"] = data["matchups"].pop(six.mid)  # the target's old id
    data["matchups"][eight.mid]["alias_of"] = "0123456789abcdef"
    index.path.write_text(json.dumps(data))
    assert flib.FlopIndex(tmp_path).rekey(m6) == 1
    fresh = flib.FlopIndex(tmp_path)
    assert fresh.alias_of(eight.mid) == six.mid
    assert fresh.boards(eight.mid) == {"Kd7s2c": {"key": "k1", "weight": 1}}


def test_alias_and_reindex_refuse_while_a_fill_runs_on_the_root(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running fill or --cached-only walk rewrites the whole index from its own snapshot when it
    ends, which would erase aliases written meanwhile."""
    import os

    from rich.console import Console

    from pn.cli import solve_cmds

    monkeypatch.setattr(solve_cmds, "console", Console(width=220))
    monkeypatch.setenv("PN_SOLVE_CACHE", str(root))
    pid = root / lf.PID_NAME
    pid.write_text(str(os.getpid()))  # a live process
    text = (root / flib.INDEX_NAME).read_text()
    for args in (["alias", "--table", "8", "--depth", "100", "--sample", "1"], ["reindex"]):
        r = CliRunner().invoke(app, ["library", *args])
        assert r.exit_code == 1 and "fill is running" in r.output, (args, r.output)
    assert (root / flib.INDEX_NAME).read_text() == text
    pid.write_text("999999999")  # a stale pid file does not block
    r = CliRunner().invoke(
        app, ["library", "alias", "--table", "8", "--depth", "100", "--sample", "1"]
    )
    assert r.exit_code == 0 and "wrote 85" in r.output, r.output
