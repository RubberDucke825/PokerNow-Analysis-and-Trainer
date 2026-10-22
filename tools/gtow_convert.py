"""Convert raw GTO Wizard grid scrapes (data/gtowizard/*_raw.json) into
(a) data/gtowizard/<name>_freqs.json — normalized per-hand action frequencies per spot
(b) config/preflop_charts/gtowizard_<depth>bb.txt — PURE charts (action freq >= 50% -> in), key = range text
Raw key format: '<depth>|<kind>|<positions...>' where kind in rfi, vs_open, vs_limp, cold, vs_<N>bet (N >= 3:
'<depth>|vs_<N>bet|<OPENER>|<3BETTOR>'; actor = opener for odd N, the 3-bettor for even N).
Cells: hand -> {actionKey: pct}. Action keys are F/C/R/A, or legacy glued legend names ('Raise 2.543.3%').
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RANKS = "AKQJT98765432"

def norm_key(k: str) -> str:
    if k in ("F", "C", "R", "A"):
        return k
    if k.startswith("Fold"): return "F"
    if k.startswith("Call") or k.startswith("Check"): return "C"
    if k.startswith("Raise"): return "R"
    if k.startswith("Allin"): return "A"
    return "?"

ACTOR_IDX = {"rfi": 2, "vs_open": 3, "vs_limp": 3, "cold": 4}

def nbet_of(kind: str) -> int | None:
    m = re.fullmatch(r"vs_(\d+)bet", kind)
    return int(m.group(1)) if m else None

def actor_of(key: str) -> str:
    parts = key.split("|")
    n = nbet_of(parts[1])
    if n is not None:
        return parts[2] if n % 2 else parts[3]  # odd: opener acts; even: 3-bettor acts
    return parts[ACTOR_IDX[parts[1]]]

def acting_chip(spot: dict, actor: str) -> dict:
    """The LAST top-bar chip for `actor` is its current decision node. Parse its actions."""
    chips = [c for c in (spot.get("chips") or []) if c.startswith(actor + " ")]
    if not chips:
        return {}
    m = re.match(r"^(UTG|HJ|CO|BTN|SB|BB)\s*([0-9.]+)\s*(.*)$", chips[-1])
    acts = re.findall(r"(Fold|Call|Check|Raise [0-9.]+|Allin [0-9.]+)", m.group(3))
    return {"pos": m.group(1), "stack": float(m.group(2)), "actions": acts}

def normalize(raw: dict) -> dict:
    out = {}
    for key, spot in raw.items():
        actor = actor_of(key)
        chip = acting_chip(spot, actor)
        if not chip and key.split("|")[1] == "rfi":  # early RFI scrapes lacked chip text; sizes verified in UI
            chip = {"pos": actor, "actions": ["Fold", "Raise 3.5" if actor == "SB" else "Raise 2.5", "Allin"]}
        cells = {}
        for hand, acts in spot["cells"].items():
            d = {}
            for k, v in acts.items():
                if k in ("w", "out"):  # reach weight (range-normalized view) / out-of-range marker: not actions
                    continue
                nk = norm_key(k)
                d[nk] = d.get(nk, 0) + v
            if sum(d.values()) < 95:  # greyed-out cell: hand not in actor's range at this node
                d = {"F": 100, "out": 1}
            cells[hand] = d
        # fill missing hands as 100% fold (extractor dropped pure-fold cells)
        hands = [RANKS[i] + RANKS[j] + s for i in range(13) for j in range(i, 13) for s in ([""] if i == j else ["s", "o"])]
        for h in hands:
            cells.setdefault(h, {"F": 100})
        # if acting chip has Check instead of Call, relabel C as X (check)
        if chip and any(a == "Check" for a in chip["actions"]):
            for h in cells:
                if "C" in cells[h]:
                    cells[h]["X"] = cells[h].pop("C")
        raise_size = next((float(a.split()[1]) for a in chip.get("actions", []) if a.startswith("Raise")), None)
        out[key] = {"actor": actor, "actions": chip.get("actions"), "raise_to_bb": raise_size, "cells": cells}
    return out

def pure_range(cells: dict, action: str) -> str:
    """Hands where `action` freq >= 50. Returns compact range text (class list)."""
    hands = [h for h, d in cells.items() if d.get(action, 0) >= 50]
    order = {h: i for i, h in enumerate(hands)}
    return ",".join(sorted(hands, key=lambda h: (RANKS.index(h[0]), RANKS.index(h[1]), h[2:] != "s")))

def chart_lines(freqs: dict, depth: str) -> list[str]:
    lines = [f"# GTO Wizard 6-max cash, cEV, {depth}bb, 2.5x opens (free solution), PURE: action freq >= 50% -> in.",
             "# Scraped from the user's own GTO Wizard free account on 2026-10-01. Keys mirror config/preflop_charts/README.md.", ""]
    for key, spot in sorted(freqs.items()):
        parts = key.split("|")
        if parts[0] != depth:
            continue
        kind = parts[1]
        actor = spot["actor"]
        c = spot["cells"]
        if kind == "rfi":
            if actor == "SB":
                # GTO Wizard's SB mixes limp/raise ~50/50 across most of its range. For a PURE chart we use the
                # standard raise-or-fold simplification: play (raise) every hand whose raise+limp freq >= 50%.
                play = [h for h, d in c.items() if d.get("R", 0) + d.get("C", 0) >= 50]
                lines.append(f"open SB = {','.join(play)}")
                lines.append("limp SB = ")
            else:
                lines.append(f"open {actor} = {pure_range(c, 'R')}")
        elif kind == "vs_open":
            opener = parts[2]
            lines.append(f"3bet {actor} vs {opener} = {pure_range(c, 'R')}")
            lines.append(f"call {actor} vs {opener} = {pure_range(c, 'C')}")
        elif nbet_of(kind) is not None:
            n = nbet_of(kind)
            opener, threebettor = parts[2], parts[3]
            me, them = (opener, threebettor) if n % 2 else (threebettor, opener)
            # the re-raise is 'R' (sized) when offered, else the all-in
            reraise = pure_range(c, "R") or pure_range(c, "A") if n % 2 else pure_range(c, "A") or pure_range(c, "R")
            lines.append(f"{n + 1}bet {me} vs{n}bet {them} = {reraise}")
            lines.append(f"call {me} vs{n}bet {them} = {pure_range(c, 'C')}")
        elif kind == "cold":
            opener, threebettor, actor2 = parts[2], parts[3], parts[4]
            lines.append(f"cold4bet {actor2} vs {opener}open {threebettor}3bet = {pure_range(c, 'R') or pure_range(c, 'A')}")
            lines.append(f"coldcall {actor2} vs {opener}open {threebettor}3bet = {pure_range(c, 'C')}")
        elif kind == "vs_limp":
            lines.append(f"raise BB vsSBlimp = {pure_range(c, 'R')}")
    return lines

def main(raw_path: str):
    raw = json.load(open(raw_path))
    freqs = normalize(raw)
    name = Path(raw_path).name.replace("_raw.json", "")
    fq = ROOT / "data/gtowizard" / f"{name}_freqs.json"
    json.dump(freqs, open(fq, "w"), indent=0, separators=(",", ":"))
    depths = sorted({k.split("|")[0] for k in freqs})
    for d in depths:
        out = ROOT / "config/preflop_charts" / f"gtowizard_{d}bb.txt"
        out.write_text("\n".join(chart_lines(freqs, d)) + "\n")
        print("wrote", out, "spots", sum(1 for k in freqs if k.startswith(d + "|")))
    print("wrote", fq)

if __name__ == "__main__":
    main(sys.argv[1])
