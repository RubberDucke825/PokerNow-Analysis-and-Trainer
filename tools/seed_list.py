"""Files another machine needs to run `pn library fill` without re-solving what this one has:
config, the flop index, the GTO Wizard frequencies (the preflop engine sizes raises from them,
and every flop spot key hashes those sizes) and the cache documents the index does not cover
(preflop solves etc.). Library documents stay home: a shard skips an indexed board by the index
alone ("finer" at the pass accuracy). Paths relative to the repo root, one per line.
Run with pn/.venv/bin/python from the repo root."""

import json
import os

from pn.solve import flops as fl
from pn.solve.request import TABLE_SEATS
from pn.train import flop_library as flib

IDX = "data/solves/cache/hu_flop_library.json"
CACHE = "data/solves/cache"
# the index keeps the finest key; skip the coarser passes' documents too (2.0: the Plan 16 200bb pass)
ACCURACIES = (2.0, 1.0, 0.5, 0.3)


LIBRARIES = ((6, 100), (8, 100), (6, 200), (8, 200))  # Plan 16: every table x depth the fill plans


def library_keys() -> set[str]:
    ms = {m.mid: m for t, d in LIBRARIES for m in flib.matchups(TABLE_SEATS[t], d, f"{t}max", sb=50, bb=100)}
    keys: set[str] = set()
    with open(IDX) as fh:
        index = json.load(fh)
    for mid, e in index["matchups"].items():
        for board, b in e.get("boards", {}).items():  # alias entries hold none
            keys.add(b["key"])
            if mid in ms:
                keys.update(flib.flop_spot(ms[mid], tuple(fl.flop_cards(board)), a)[2] for a in ACCURACIES)
    return keys


def main() -> None:
    skip = library_keys()
    print("config")
    print(IDX)
    print("data/gtowizard/cash6m_cev_2.5x_freqs.json")
    for f in sorted(os.listdir(CACHE)):
        stem = os.path.splitext(f.removesuffix(".gz"))[0]  # <key>.json.gz (Plan 17) or <key>.json
        if f.startswith(".") or f.endswith((".tmp", ".log")) or f == os.path.basename(IDX) or stem in skip:
            continue
        print(f"{CACHE}/{f}")


if __name__ == "__main__":
    main()
