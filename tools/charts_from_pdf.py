"""Extract the PokerCoaching.com preflop charts (9-handed, 100bb, ante, PURE) from their PDF into
a `config/preflop_charts` file (`<key> = <range text>`, pn.ranges syntax).

Usage (from pn/, so the project venv has Pillow/numpy):
    uv run python ../tools/charts_from_pdf.py ~/Downloads/preflop-charts.pdf \
        --out ../config/preflop_charts/pokercoaching_100bb.txt

How it works:
- `pdftohtml -xml` (poppler) writes every chart image of pages 3-14 together with its position on
  the page and the page/chart title text, so each image is matched to the title just above it.
- Each image is a 13x13 grid (pairs on the diagonal, suited above, offsuit below, A..2). The grid
  is located from the white gaps between cells (13 column runs, then the first 13 row runs), and
  each cell is classified by the median colour of its inner area (label text is a minority of the
  pixels, so the median is the fill): red = raise / 3-bet / 4-bet for value, blue = the same as a
  bluff, green = call (limp on the SB RFI chart), dark grey = opened but folds to the 3-bet,
  white = not in range.
- Every chart is checked against the combo counts printed in its legend (EXPECTED below). The
  counts are NOT in the PDF text layer; they were read off the legend images once by eye and
  hard-coded. Any mismatch aborts the run (exit 1) without writing the output.

Keys (positions in our names: UTG UTG1 UTG2 LJ HJ CO BTN SB BB; "X vs A/B" is stored per opener):
    open <P>                    raise first in (value + bluff); SB also open_value/open_bluff SB
    limp SB                     SB RFI limp range (SB is limp/raise in this source)
    3bet|call <H> vs <O>        facing an open; 3bet = value + bluff (3bet_value / 3bet_bluff too)
    4bet|call <O> vs3bet <T>    opener facing a 3-bet; 4bet = value + bluff (4bet_value/_bluff)
    3bet|call SB limp_vs_raise BB   SB limped, BB raised (3bet_value/_bluff too)
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

RANKS = "AKQJT98765432"
GRID = 13
TOTAL_COMBOS = 1326
# Reference fills measured from the PDF (cell medians cluster tightly around these).
PALETTE = {
    "red": (240, 75, 80),
    "blue": (40, 40, 210),
    "green": (20, 155, 20),
    "grey": (70, 70, 70),
    "white": (245, 245, 245),
}
NONWHITE = 235  # a pixel with any channel below this is not page background
MIN_RUN = 20  # cells are ~42px; shorter runs are text or borders
CELL_INSET = 6  # px trimmed off each cell side before sampling (skips the rounded border)
FIRST_PAGE, LAST_PAGE = 3, 14
TITLE_FONT_SIZE = "24"

POS = {"UTG": "UTG", "UTG+1": "UTG1", "UTG+2": "UTG2", "LJ": "LJ", "Lojack": "LJ", "HJ": "HJ",
       "Hijack": "HJ", "CO": "CO", "Cutoff": "CO", "BTN": "BTN", "Button": "BTN", "SB": "SB",
       "Small Blind": "SB", "BB": "BB"}  # fmt: skip

# Combo counts per colour, read BY EYE from each chart's legend ("118 / 934" -> 118) on 2026-10-10,
# because the PDF text layer does not carry them. Keyed by (page, chart title as in the text
# layer); values are (red, blue, green, grey) combos. white is implied (1326 - the rest), which
# equals the legend's white "Fold x / 1326" row for every chart.
_LEGEND: dict[tuple[int, str], tuple[int, int, int, int]] = {
    (3, "UTG"): (134, 0, 0, 0), (3, "UTG+1"): (190, 0, 0, 0), (3, "UTG+2"): (208, 0, 0, 0),
    (3, "Lojack"): (242, 0, 0, 0), (3, "Hijack"): (282, 0, 0, 0), (3, "Cutoff"): (358, 0, 0, 0),
    (3, "Button"): (678, 0, 0, 0), (3, "Small Blind"): (118, 172, 644, 0),
    (4, "UTG+1 vs UTG"): (34, 20, 44, 0), (4, "UTG+2 vs UTG/UTG+1"): (34, 20, 44, 0),
    (4, "LJ vs UTG/UTG+1"): (34, 24, 54, 0), (4, "LJ vs UTG+2"): (34, 32, 60, 0),
    (4, "HJ vs UTG"): (34, 24, 54, 0), (4, "HJ vs UTG+1"): (34, 24, 64, 0),
    (4, "HJ vs UTG+2"): (34, 40, 70, 0), (4, "HJ vs LJ"): (58, 60, 78, 0),
    (5, "CO vs UTG/UTG+1"): (38, 32, 76, 0), (5, "CO vs UTG+2"): (38, 48, 82, 0),
    (5, "CO vs LJ"): (58, 56, 80, 0), (5, "CO vs HJ"): (58, 80, 84, 0),
    (6, "BTN vs UTG"): (34, 48, 108, 0), (6, "BTN vs UTG+1"): (34, 52, 116, 0),
    (6, "BTN vs UTG+2"): (50, 52, 136, 0), (6, "BTN vs LJ"): (50, 60, 140, 0),
    (6, "BTN vs HJ"): (54, 68, 152, 0), (6, "BTN vs CO"): (54, 92, 184, 0),
    (7, "SB vs UTG/UTG+1"): (22, 12, 86, 0), (7, "SB vs UTG+2"): (22, 12, 104, 0),
    (7, "SB vs LJ"): (38, 28, 94, 0), (7, "SB vs HJ"): (38, 40, 104, 0),
    (7, "SB vs CO"): (74, 148, 0, 0), (7, "SB vs BTN"): (102, 180, 0, 0),
    (8, "BB vs UTG/UTG+1"): (26, 28, 316, 0), (8, "BB vs UTG+2"): (44, 40, 314, 0),
    (8, "BB vs LJ"): (58, 44, 336, 0), (8, "BB vs HJ"): (70, 56, 320, 0),
    (8, "BB vs CO"): (106, 84, 392, 0), (8, "BB vs BTN"): (124, 172, 658, 0),
    (8, "BB vs SB"): (124, 188, 726, 0),
    (9, "UTG vs UTG+1 3bet"): (28, 16, 44, 46), (9, "UTG vs UTG+2 3bet"): (28, 20, 50, 36),
    (9, "UTG vs LJ 3bet"): (28, 20, 54, 32), (9, "UTG vs HJ 3bet"): (34, 24, 58, 18),
    (9, "UTG vs CO/BTN 3bet"): (34, 20, 74, 6), (9, "UTG vs SB/BB 3bet"): (34, 20, 80, 0),
    (10, "UTG+1 vs UTG+2 3bet"): (28, 36, 64, 74), (10, "UTG+1 vs LJ 3bet"): (28, 32, 80, 62),
    (10, "UTG+1 vs HJ/CO 3bet"): (28, 44, 94, 36), (10, "UTG+1 vs BTN 3bet"): (28, 48, 106, 20),
    (10, "UTG+1 vs SB/BB 3bet"): (34, 48, 108, 12),
    (11, "UTG+2 vs LJ 3bet"): (28, 44, 84, 52), (11, "UTG+2 vs HJ 3bet"): (28, 48, 90, 42),
    (11, "UTG+2 vs CO/BTN 3bet"): (28, 48, 108, 24),
    (11, "UTG+2 vs SB/BB 3bet"): (34, 48, 102, 24),
    (12, "LJ vs HJ 3bet"): (34, 48, 86, 74), (12, "LJ vs CO 3bet"): (40, 52, 86, 64),
    (12, "LJ vs BTN 3bet"): (40, 56, 96, 50), (12, "LJ vs SB 3bet"): (40, 56, 128, 18),
    (12, "LJ vs BB 3bet"): (40, 56, 106, 40),
    (13, "HJ vs CO 3bet"): (40, 60, 132, 50), (13, "HJ vs BTN 3bet"): (40, 68, 138, 36),
    (13, "HJ vs SB 3bet"): (40, 56, 142, 44), (13, "HJ vs BB 3bet"): (40, 56, 106, 80),
    (13, "CO vs BTN/SB 3bet"): (46, 64, 152, 96), (13, "CO vs BB 3bet"): (46, 56, 144, 112),
    (14, "BTN vs SB/BB 3bet"): (72, 124, 246, 236), (14, "SB RFI vs BB 3bet"): (36, 52, 98, 104),
    (14, "SB Limp vs BB Raise"): (24, 60, 500, 60),
}  # fmt: skip
EXPECTED: dict[tuple[int, str], dict[str, int]] = {
    k: dict(zip(("red", "blue", "green", "grey"), v, strict=True)) for k, v in _LEGEND.items()
}


@dataclass(frozen=True)
class Chart:
    page: int
    heading: str
    title: str
    image: Path


# ------------------------------------------------------------------ PDF layout


def extract(pdf: Path, workdir: Path) -> list[Chart]:
    """Run pdftohtml and pair every chart image with the chart title just above it."""
    exe = shutil.which("pdftohtml") or "/opt/homebrew/bin/pdftohtml"
    xml_base = workdir / "pc"
    subprocess.run([exe, "-xml", "-q", "-f", str(FIRST_PAGE), "-l", str(LAST_PAGE), str(pdf),
                    str(xml_base)], check=True)  # fmt: skip
    root = ET.parse(f"{xml_base}.xml").getroot()
    title_fonts = {f.get("id") for f in root.iter("fontspec") if f.get("size") == TITLE_FONT_SIZE}
    charts: list[Chart] = []
    for page in root.iter("page"):
        texts = [(t, "".join(t.itertext()).strip()) for t in page.iter("text")]
        heading = next(s for t, s in texts if t.get("font") not in title_fonts and s)
        titles = [(int(t.get("top")), int(t.get("left")) + int(t.get("width")) / 2, s)
                  for t, s in texts if t.get("font") in title_fonts]  # fmt: skip
        for img in page.iter("image"):
            top = int(img.get("top"))
            mid = int(img.get("left")) + int(img.get("width")) / 2
            above = [t for t in titles if t[0] < top]
            if not above:
                raise SystemExit(f"page {page.get('number')}: image without a title above it")
            title = min(above, key=lambda t: (top - t[0]) + abs(mid - t[1]))[2]
            src = Path(img.get("src"))
            charts.append(Chart(int(page.get("number")), heading, title,
                                src if src.is_absolute() else workdir / src.name))  # fmt: skip
    return charts


# ------------------------------------------------------------------ grid reading


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of each run of True values at least MIN_RUN long."""
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= MIN_RUN:
                out.append((start, i))
            start = None
    return out


def grid_boxes(rgb: np.ndarray) -> list[list[tuple[int, int, int, int]]]:
    """13x13 cell boxes (y0, y1, x0, x1) found from the white gaps between cells."""
    ink = rgb.min(axis=2) < NONWHITE
    h = rgb.shape[0]
    cols = _runs(ink[: int(h * 0.6)].mean(axis=0) > 0.02)
    if len(cols) != GRID:
        raise ValueError(f"expected {GRID} cell columns, found {len(cols)}")
    rows = _runs(ink[:, cols[0][0] : cols[-1][1]].mean(axis=1) > 0.02)[:GRID]
    if len(rows) != GRID:
        raise ValueError(f"expected {GRID} cell rows, found {len(rows)}")
    sizes = [b - a for a, b in cols + rows]
    if max(sizes) - min(sizes) > 4:
        raise ValueError(f"irregular cell sizes {sizes}")
    return [[(y0, y1, x0, x1) for x0, x1 in cols] for y0, y1 in rows]


def classify(rgb: np.ndarray) -> str:
    """Palette colour nearest to the median of an (n, 3) or (h, w, 3) pixel block."""
    med = np.median(np.asarray(rgb, dtype=float).reshape(-1, 3), axis=0)
    return min(PALETTE, key=lambda k: float(np.sum((med - np.array(PALETTE[k])) ** 2)))


def read_grid(rgb: np.ndarray) -> dict[str, str]:
    """Hand class (AA, AKs, AKo, ...) -> colour."""
    out = {}
    for r, row in enumerate(grid_boxes(rgb)):
        for c, (y0, y1, x0, x1) in enumerate(row):
            i = CELL_INSET
            out[cell_class(r, c)] = classify(rgb[y0 + i : y1 - i, x0 + i : x1 - i])
    return out


def cell_class(r: int, c: int) -> str:
    if r == c:
        return RANKS[r] * 2
    hi, lo = min(r, c), max(r, c)
    return RANKS[hi] + RANKS[lo] + ("s" if c > r else "o")


def n_combos(cls: str) -> int:
    return 6 if len(cls) == 2 else 4 if cls.endswith("s") else 12


def combos(classes: list[str]) -> int:
    return sum(n_combos(c) for c in classes)


# ------------------------------------------------------------------ chart -> keys


def range_text(classes: list[str]) -> str:
    order = {cell_class(r, c): r * GRID + c for r in range(GRID) for c in range(GRID)}
    return ",".join(sorted(classes, key=order.__getitem__))


def _seats(text: str) -> list[str]:
    try:
        return [POS[s] for s in text.split("/")]
    except KeyError as e:
        raise SystemExit(f"unknown seat in {text!r}") from e


def chart_keys(chart: Chart, colours: dict[str, str]) -> list[tuple[str, list[str]]]:
    """(key, classes) lines for one chart."""
    by = {k: [c for c, v in colours.items() if v == k] for k in PALETTE}
    agg = by["red"] + by["blue"]
    t = chart.title
    if chart.heading.startswith("Raise First In"):
        p = _seats(t)[0]
        if p != "SB":
            if by["blue"] or by["green"] or by["grey"]:
                raise SystemExit(f"{t}: unexpected non-raise colour in an RFI chart")
            return [(f"open {p}", agg)]
        return [(f"open {p}", agg), (f"open_value {p}", by["red"]),
                (f"open_bluff {p}", by["blue"]), (f"limp {p}", by["green"])]  # fmt: skip
    if m := re.fullmatch(r"SB Limp vs BB Raise", t):
        return [("3bet SB limp_vs_raise BB", agg), ("3bet_value SB limp_vs_raise BB", by["red"]),
                ("3bet_bluff SB limp_vs_raise BB", by["blue"]),
                ("call SB limp_vs_raise BB", by["green"])]  # fmt: skip
    if m := re.fullmatch(r"(\S+)(?: RFI)? vs (\S+) 3bet", t):
        o = _seats(m.group(1))[0]
        out = []
        for b in _seats(m.group(2)):
            out += [(f"4bet {o} vs3bet {b}", agg), (f"4bet_value {o} vs3bet {b}", by["red"]),
                    (f"4bet_bluff {o} vs3bet {b}", by["blue"]),
                    (f"call {o} vs3bet {b}", by["green"])]  # fmt: skip
        return out
    if m := re.fullmatch(r"(\S+) vs (\S+)", t):
        h = _seats(m.group(1))[0]
        out = []
        for o in _seats(m.group(2)):
            out += [
                (f"3bet {h} vs {o}", agg),
                (f"3bet_value {h} vs {o}", by["red"]),
                (f"3bet_bluff {h} vs {o}", by["blue"]),
                (f"call {h} vs {o}", by["green"]),
            ]
        return out
    raise SystemExit(f"page {chart.page}: unrecognised chart title {t!r}")


# ------------------------------------------------------------------ verification


def counts(colours: dict[str, str]) -> dict[str, int]:
    out = {k: combos([c for c, v in colours.items() if v == k]) for k in PALETTE}
    return {k: v for k, v in out.items() if v}


def verify(chart: Chart, got: dict[str, int]) -> str | None:
    """Error text when the extracted counts differ from the printed legend."""
    want = EXPECTED.get((chart.page, chart.title))
    if want is None:
        return "no expected counts recorded"
    full = {**want, "white": TOTAL_COMBOS - sum(want.values())}
    full = {k: v for k, v in full.items() if v}
    return None if got == full else f"extracted {got} != legend {full}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    lines, errors, table = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        charts = extract(args.pdf, Path(tmp))
        for ch in charts:
            colours = read_grid(np.asarray(Image.open(ch.image).convert("RGB")).astype(int))
            got = counts(colours)
            err = verify(ch, got)
            table.append((ch, got, err))
            if err:
                errors.append(f"p{ch.page} {ch.title!r}: {err}")
            lines += chart_keys(ch, colours)
    for ch, got, err in table:
        print(f"p{ch.page:<3}{ch.title:<24}{got}  {'OK' if err is None else 'MISMATCH'}")
    missing = set(EXPECTED) - {(ch.page, ch.title) for ch, _, _ in table}
    errors += [f"chart not found in the PDF: p{p} {t!r}" for p, t in sorted(missing)]
    if errors:
        print("\n".join(["VERIFICATION FAILED:", *errors]), file=sys.stderr)
        return 1
    keys = [k for k, _ in lines]
    if len(keys) != len(set(keys)):
        dup = sorted({k for k in keys if keys.count(k) > 1})
        print(f"duplicate keys: {dup}", file=sys.stderr)
        return 1
    header = [
        "# PokerCoaching.com preflop charts, 9-handed cash, 100bb with ante, PURE (as published).",
        f"# Generated by tools/charts_from_pdf.py from {args.pdf.name}; every chart verified",
        "# against the combo counts printed in its legend. Do not edit by hand; re-run the tool.",
        "# Positions in our names (UTG UTG1 UTG2 LJ HJ CO BTN SB BB). Keys: see the tool docstring.",
        "",
    ]
    body = [f"{k} = {range_text(v)}" for k, v in lines]
    args.out.write_text("\n".join(header + body) + "\n")
    print(f"wrote {len(body)} keys from {len(table)} charts to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
