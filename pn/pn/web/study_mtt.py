"""Tournament preflop study (contracts/study_preflop.md, "Tournament charts"): walk Upswing's 8-handed MTT chart
tree (data/upswing/8m_mtt_50_free_freqs.json, tools/upswing_convert.py) at 10, 25 or 50bb. Preflop only: a line
that reaches the flop, or a spot the free chart set does not cover, ends the walk; nothing is solved.

Each chart links every action label to the next chart (`children`, null = no next chart). A line is the list of
option indices taken from the depth's root chart (UTG opens), so `path=2,0` = UTG folds, UTG+1 picks option 0.
Upswing names the early seats EP / MP; they are shown as UTG / UTG+1, like the 8-max trainer seats."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from pn import ranges
from pn.profile.paths import repo_root

router = APIRouter()
DEPTHS = (10, 25, 50)
SEAT_NAMES = {"EP": "UTG", "MP": "UTG+1"}
_SEAT_RE = re.compile(r"\b(EP|MP)\b")
END_NOTES = {
    "flop": "Flop: preflop study only, no postflop solve.",
    "over": "Hand over preflop.",
    "no_chart": "Upswing's free charts stop here: no chart for the next decision.",
}


def freqs_path() -> Path:
    return repo_root() / "data" / "upswing" / "8m_mtt_50_free_freqs.json"


@lru_cache(maxsize=1)
def _freqs() -> dict[str, Any]:
    path = freqs_path()
    return json.loads(path.read_text()) if path.exists() else {}


def chart(node: str) -> dict[str, Any] | None:
    return _freqs().get(node)


def seat_text(text: str) -> str:
    """Upswing seat names -> ours ('MP vs EP 2bb RFI' -> 'UTG+1 vs UTG 2bb RFI')."""
    return _SEAT_RE.sub(lambda m: SEAT_NAMES[m.group(1)], text)


def token(label: str) -> str:
    """Grid / colour token of an action label; raises keep their size so two sizes stay apart."""
    if label.startswith("Raise "):
        return "raise" + label[len("Raise ") :].removesuffix("bb")
    return {"All In": "allin", "Call": "call", "Check": "check", "Fold": "fold"}[label]


def _shares(
    ch: dict[str, Any],
) -> tuple[dict[str, dict[str, float]], dict[str, float], list[float]]:
    """(cells class -> token -> conditional share, reach class -> 0..1, per-option share of the hands that
    reach the node, weighted by combos)."""
    toks = [token(lb) for lb in ch["labels"]]
    by_label = dict(zip(ch["labels"], toks, strict=True))
    cells: dict[str, dict[str, float]] = {}
    reach: dict[str, float] = {}
    totals = dict.fromkeys(toks, 0.0)
    n = 0.0
    for cls in ranges.all_classes():
        r = float(ch["reach"].get(cls, 0.0)) / 100
        cell = ch["cells"].get(cls) or {}
        total = sum(cell.values())
        dist = (
            {by_label[lb]: v / total for lb, v in cell.items() if v > 0}
            if r > 0 and total
            else {"fold": 1.0}
        )
        cells[cls] = {t: round(f, 4) for t, f in dist.items()}
        reach[cls] = round(r, 4)
        w = ranges.class_n_combos(cls) * r
        n += w
        for t, f in dist.items():
            totals[t] = totals.get(t, 0.0) + w * f
    return cells, reach, [round(totals[t] / n, 4) if n else 0.0 for t in toks]


def options(ch: dict[str, Any]) -> list[dict[str, Any]]:
    freqs = _shares(ch)[2]
    return [{"k": k, "label": lb, "token": token(lb), "freq": freqs[k], "next": ch["children"][k] is not None}
            for k, lb in enumerate(ch["labels"])]  # fmt: skip


def _actor(ch: dict[str, Any]) -> str:
    return seat_text(ch["title"].split()[0])


def _end_kind(label: str) -> str:
    if label in ("Call", "Check"):
        return "flop"
    return "over" if label == "Fold" else "no_chart"


def _parse(path: str) -> list[int]:
    steps = []
    for n, raw in enumerate((s.strip() for s in path.split(",") if s.strip()), 1):
        if not raw.isdigit():
            raise HTTPException(400, f"step {n}: expected an option number, got {raw!r}")
        steps.append(int(raw))
    return steps


def walk(
    depth: int, path: list[int]
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
    """(current chart id or None, steps taken, end of line or None) for a path of option indices."""
    node: str | None = f"{depth}bb_RFI_EP"
    steps: list[dict[str, Any]] = []
    end = None
    for n, k in enumerate(path, 1):
        ch = chart(node) if node else None
        if ch is None:
            raise HTTPException(400, f"step {n}: the line is over")
        if k >= len(ch["labels"]):
            raise HTTPException(400, f"step {n}: no option {k} at {seat_text(ch['title'])!r}")
        label = ch["labels"][k]
        steps.append({"actor": _actor(ch), "label": label, "node": node})
        node = ch["children"][k]
        if node is None:
            kind = _end_kind(label)
            end = {"kind": kind, "actor": _actor(ch), "label": label, "note": END_NOTES[kind]}
    return node, steps, end


@router.get("/api/study/mtt")
def study_mtt(depth: int = Query(10), path: str = "") -> dict[str, Any]:
    if depth not in DEPTHS:
        raise HTTPException(400, "depth must be 10, 25 or 50")
    if not _freqs():
        raise HTTPException(404, f"missing {freqs_path()}: run tools/upswing_convert.py")
    taken = _parse(path)
    node, steps, end = walk(depth, taken)
    out: dict[str, Any] = {"depth": depth, "path": ",".join(map(str, taken)), "steps": steps, "node": node,
                           "end": end, "actor": None, "title": None, "options": [], "cells": None, "reach": None}  # fmt: skip
    ch = chart(node) if node else None
    if ch is not None:
        cells, reach, _ = _shares(ch)
        out.update(
            actor=_actor(ch),
            title=seat_text(ch["title"]),
            options=options(ch),
            cells=cells,
            reach=reach,
        )
    return out
