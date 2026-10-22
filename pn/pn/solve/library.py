"""Precomputed preflop root documents per table/depth/source (read-only since Plan 7: no engine
solves preflop, so roots are only read when an old file exists). The heads-up flop library the
trainer deals from is filled by pn.train.library_fill."""

from __future__ import annotations

from pathlib import Path

from pn import settings


def library_dir() -> Path:
    return settings.library_dir()


def library_path(table: int, depth_bb: int, source: str) -> Path:
    """The root document `<library dir>/<table>max_<depth>bb_<source>.json`."""
    return library_dir() / f"{table}max_{depth_bb}bb_{source}.json"
