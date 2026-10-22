"""Seed a fake texas heads-up flop library (pn.train.flop_library) into a SolveCache root:
fake_tree documents labelled `texas` for every matchup x BOARDS, recorded in the flop index.
`dcfr_runner` is the pre-Plan 7 name of `texas_runner`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from fake_tree import fake_doc

from pn.solve import SolveCache
from pn.solve import flops as fl
from pn.solve.request import TABLE_SEATS
from pn.train import flop_library as flib

BOARDS = ("Kd7s2c", "Ah8c3d", "QsJh4c", "9c6d5h")


def texas_runner(spot, backend=None):
    return {**fake_doc(spot), "meta": {"backend": "texas", "ev_available": False}}, "texas"


dcfr_runner = texas_runner


def seed(root: Path, *, table: int = 6, depth: int = 100, freqs: Mapping | None = None,
         boards: Sequence[str] = BOARDS, pots: Sequence[str] | None = None) -> flib.FlopIndex:
    cache = SolveCache(root, runner=texas_runner)
    index = flib.FlopIndex(root)
    for m in flib.matchups(TABLE_SEATS[table], depth, f"{table}max", sb=50, bb=100, freqs=freqs):
        if pots is not None and m.pot not in pots:
            continue
        for b in boards:
            _, spot, key = flib.flop_spot(m, tuple(fl.flop_cards(b)))
            cache.solve(spot, "texas")
            index.record(m, b, key, 1)
    return index


def copy_into(template: Path, root: Path) -> None:
    """Copy a seeded cache root (documents + flop index) into `root`."""
    import shutil

    shutil.copytree(template, root, dirs_exist_ok=True)
