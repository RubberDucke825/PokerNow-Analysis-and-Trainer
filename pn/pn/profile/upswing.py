"""Preflop charts from the Upswing Poker Preflop Prodigy crawl (data/upswing/9m_live_cash_free_freqs.json,
written by tools/upswing_convert.py).

Each chart (keyed by the viewer's own id, e.g. 'vs RFI_UTG RFI_EP_vs 3bb') holds `labels` ('Raise 9bb', 'All In',
'Call', 'Fold'), `size_bb` (the raise-to size or null), `cells` = {hand class: {label: conditional pct}} and
`reach` = {hand class: pct of the hand that reaches the node}; every one of the 169 classes has both.

Upswing is 9-handed, 100bb: seats UTG EP MP LJ HJ CO BTN SB BB. Our 6-max seats LJ..BB are Upswing's own; our
8-max drops Upswing UTG (UTG -> EP, UTG1 -> MP), and an ingested 7-9 handed game names its early seats from the
front, so the EP seat with as many players behind is used (as charts._pc_seat does).

Chart kinds follow pn.train.preflop.Situation: ("rfi", P), ("vs_open", O, H) (+ `caller` -> squeeze),
("vs_3bet", O, T) actor O, ("cold", O, T, H), ("vs_4bet", O, T) actor T (`jam` picks the all-in 4-bet chart),
("vs_5bet", O, T) actor O. Upswing has no limp lines.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pn.profile.charts import normalize_position
from pn.profile.paths import repo_root

SEATS = ("UTG", "EP", "MP", "LJ", "HJ", "CO", "BTN", "SB", "BB")
EP_SEATS = ("UTG", "EP", "MP")  # Upswing seats in front of LJ
OUR_EP = ("UTG", "UTG1", "UTG2")  # our 8-max / ingest names for them, from the front
JAM_SIZE = "100bb"


def freqs_path() -> Path:
    return repo_root() / "data" / "upswing" / "9m_live_cash_free_freqs.json"


@lru_cache(maxsize=1)
def _freqs() -> dict:
    path = freqs_path()
    return json.loads(path.read_text()) if path.exists() else {}


def load_freqs(path: Path | None = None) -> dict:
    return json.loads(path.read_text()) if path else _freqs()


def has_freqs() -> bool:
    return bool(_freqs())


def chart(chart_id: str | None) -> dict | None:
    return _freqs().get(chart_id) if chart_id else None


def seat(position: str, table: str, players: int | None = None) -> str | None:
    """Upswing seat for one of our positions, or None (heads-up, unknown seat)."""
    if table == "hu":
        return None
    pos = normalize_position(position, table)
    if table == "8max" and pos in OUR_EP:
        i = OUR_EP.index(pos) + 9 - (players or 8)
        pos = EP_SEATS[min(max(i, 0), len(EP_SEATS) - 1)]
    return pos if pos in SEATS else None


@lru_cache(maxsize=1)
def _by_prefix() -> dict[str, tuple[str, ...]]:
    """Chart id up to and including '_vs ' -> the ids sharing it (the trailing size varies)."""
    index: dict[str, list[str]] = {}
    for cid in _freqs():
        if "_vs " in cid:
            index.setdefault(cid[: cid.index("_vs ") + 4], []).append(cid)
    return {k: tuple(sorted(v)) for k, v in index.items()}


def _one(prefix: str, *, jam: bool | None = None) -> str | None:
    ids = _by_prefix().get(prefix, ())
    if jam is not None:
        ids = tuple(c for c in ids if c.endswith(JAM_SIZE) == jam)
    return ids[0] if len(ids) == 1 else None


def chart_id(kind: str | None, seats: tuple[str, ...], table: str, *, players: int | None = None,
             caller: str | None = None, jam: bool = False) -> str | None:  # fmt: skip
    """Upswing chart id for a situation (seats are OUR positions), or None when Upswing has no such chart."""
    s = [seat(p, table, players) for p in seats]
    if not s or None in s:
        return None
    found: str | None = None
    if kind == "rfi" and len(s) == 1:
        found = f"RFI_{s[0]}"
    elif kind == "vs_open" and len(s) == 2:
        c = seat(caller, table, players) if caller else None
        if caller and c is None:
            return None
        found = (
            f"Squeeze_{s[0]} RFI_to 3bb_{c} call_{s[1]}"
            if c
            else f"vs RFI_{s[0]} RFI_{s[1]}_vs 3bb"
        )
    elif kind == "vs_3bet" and len(s) == 2:
        found = _one(f"vs 3Bet_{s[1]} 3Bet_{s[0]}_vs ")
    elif kind == "cold" and len(s) == 3:
        found = _one(f"vs 3Bet (cold)_{s[0]} RFI_{s[1]} 3Bet_{s[2]}_vs ")
    elif kind == "vs_4bet" and len(s) == 2:
        found = _one(f"vs 4Bet_{s[0]} 4Bet_{s[1]}_vs ", jam=jam)
    elif kind == "vs_5bet" and len(s) == 2:
        found = f"vs 5Bet_{s[1]} 5Bet_{s[0]}_vs {JAM_SIZE}"
    return found if found in _freqs() else None


def code(label: str) -> str:
    """Action code as pn.train.preflop.CODES: R raise, A all-in, C call, F fold."""
    if label.startswith("Raise"):
        return "R"
    return {"All In": "A", "Call": "C", "Fold": "F"}[label]


def labels(ch: dict) -> dict[str, str]:
    return {code(lb): lb for lb in ch["labels"]}


def reach(ch: dict, cls: str) -> float:
    return float(ch["reach"].get(cls, 0.0)) / 100


def class_dist(ch: dict, cls: str) -> dict[str, float]:
    """Conditional action shares {code: share} of a hand class at this node (a non-reaching hand folds)."""
    cell = ch["cells"].get(cls) or {}
    if reach(ch, cls) <= 0 or not cell:
        return {"F": 1.0}
    dist: dict[str, float] = {}
    for lb, pct in cell.items():
        dist[code(lb)] = dist.get(code(lb), 0.0) + float(pct)
    total = sum(dist.values())
    return {k: v / total for k, v in dist.items() if v > 0}


def raise_to_bb(ch: dict) -> float | None:
    for lb in ch["labels"]:
        if lb.startswith("Raise ") and lb.endswith("bb"):
            return float(lb[len("Raise ") : -len("bb")])
    return None
