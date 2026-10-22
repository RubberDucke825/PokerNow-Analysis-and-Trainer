"""PokerCoaching charts: generated file matches the PDF legends, loader consults it first, and the
PDF extractor's grid reader / colour classifier round-trips a synthetic chart."""

from __future__ import annotations

import importlib.util
import sys

import numpy as np
import pytest

from pn import ranges
from pn.profile import charts
from pn.profile.paths import repo_root


def _pc(key: str) -> dict:
    return ranges.parse(charts.chart_entries(charts.PC_FILE)[key])


def _gtow(key: str) -> dict:
    return ranges.parse(charts.chart_entries("gtowizard_100bb.txt")[key])


@pytest.fixture(autouse=True)
def _need_files() -> None:
    for name in (charts.PC_FILE, "gtowizard_100bb.txt"):
        if not (charts.charts_dir() / name).exists():
            pytest.skip(f"{name} not present")


@pytest.mark.parametrize(
    "key,combos",
    [
        ("open BTN", 678),  # 51.1%
        ("open UTG", 134),
        ("open UTG1", 190),
        ("open SB", 118 + 172),
        ("limp SB", 644),
        ("call BB vs BTN", 658),
        ("3bet_value SB vs BTN", 102),
        ("3bet_bluff SB vs BTN", 180),
        ("3bet SB vs BTN", 282),
        ("call SB vs BTN", 0),
        ("4bet BTN vs3bet BB", 72 + 124),
        ("call BTN vs3bet SB", 246),
        ("call SB limp_vs_raise BB", 500),
    ],
)
def test_generated_file_matches_pdf_legend(key: str, combos: int) -> None:
    assert len(_pc(key)) == combos


def test_shared_charts_are_stored_per_opener() -> None:
    assert _pc("3bet SB vs UTG") == _pc("3bet SB vs UTG1")  # "SB vs UTG/UTG+1"
    assert _pc("call UTG vs3bet CO") == _pc("call UTG vs3bet BTN")  # "UTG vs CO/BTN 3bet"


def test_pokercoaching_wins_over_gto_wizard() -> None:
    btn = charts.chart_range("6max", "open", "BTN")
    assert btn == _pc("open BTN") and btn != _gtow("open BTN")
    assert charts.chart_range("6max", "open", "BTN", depth_bb=200) == btn  # any depth
    assert charts.chart_range("6max", "open", "UTG") == _pc("open LJ")  # 6-max UTG label -> LJ
    assert charts.chart_range("8max", "open", "UTG1") == _pc("open UTG2")  # 8-max seat shift
    assert charts.chart_range("6max", "3bet", "SB", "BTN") == _pc("3bet SB vs BTN")
    assert charts.chart_range("6max", "call", "SB", "BTN") == {}  # PC: SB 3-bets or folds
    assert charts.chart_range("8max", "call_3bet", "UTG2", "BB") == _pc("call UTG2 vs3bet BB")
    assert charts.chart_range("6max", "limp", "SB") == _pc("limp SB")
    assert charts.chart_range("6max", "3bet_limp_vs_raise", "SB", "BB") == _pc(
        "3bet SB limp_vs_raise BB"
    )


def test_gaps_fall_back_to_gto_wizard_then_hand_written() -> None:
    assert charts.pc_range("6max", "5bet", "BB", "BTN") is None
    assert charts.chart_range("6max", "5bet", "BB", "BTN") == _gtow("5bet BB vs4bet BTN")
    assert charts.chart_range("6max", "call_4bet", "SB", "CO") == _gtow("call SB vs4bet CO")
    hand = charts.load_table("6max")
    assert charts.chart_range("6max", "4bet", "CO") == ranges.parse(hand["4bet vs3bet CO"])
    assert charts.pc_range("hu", "open", "BTN") is None
    assert charts.chart_range("hu", "open", "BTN") == ranges.parse(
        charts.load_table("hu")["open BTN"]
    )


# ------------------------------------------------------------------ PDF extractor


@pytest.fixture(scope="module")
def tool():
    path = repo_root() / "tools" / "charts_from_pdf.py"
    pytest.importorskip("PIL")
    spec = importlib.util.spec_from_file_location("charts_from_pdf", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["charts_from_pdf"] = mod
    spec.loader.exec_module(mod)
    return mod


def _synthetic_chart(colours: dict[str, str], tool) -> np.ndarray:
    """A 13x13 grid like the PDF's: 42px cells on a 45px pitch, grey border, black label."""
    cell, pitch, x0, y0 = 42, 45, 12, 8
    img = np.full((y0 + 13 * pitch + 120, x0 + 13 * pitch + 10, 3), 255, dtype=np.uint8)
    for r in range(13):
        for c in range(13):
            y, x = y0 + r * pitch, x0 + c * pitch
            img[y : y + cell, x : x + cell] = (150, 150, 150)  # border
            img[y + 2 : y + cell - 2, x + 2 : x + cell - 2] = tool.PALETTE[
                colours[tool.cell_class(r, c)]
            ]
            img[y + 16 : y + 26, x + 10 : x + 32 : 3] = (0, 0, 0)  # "label" strokes
    legend_y = y0 + 13 * pitch + 40
    img[legend_y : legend_y + 38, x0 : x0 + 13 * pitch] = tool.PALETTE["red"]  # legend bar
    return img


def test_classifier_round_trip(tool) -> None:
    names = list(tool.PALETTE)
    classes = [tool.cell_class(r, c) for r in range(13) for c in range(13)]
    colours = {cls: names[i % len(names)] for i, cls in enumerate(classes)}
    got = tool.read_grid(_synthetic_chart(colours, tool).astype(int))
    assert got == colours
    assert tool.counts(got) == {k: tool.combos([c for c in classes if colours[c] == k])
                                for k in names}  # fmt: skip


def test_verify_flags_mismatch(tool) -> None:
    ch = tool.Chart(3, "Raise First In (RFI)", "UTG", repo_root())
    assert tool.verify(ch, {"red": 134, "white": 1192}) is None
    assert "legend" in tool.verify(ch, {"red": 130, "white": 1196})
