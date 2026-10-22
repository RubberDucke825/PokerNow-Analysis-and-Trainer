"""Preflop range-width inference."""

from __future__ import annotations

import json
from pathlib import Path

from pn import ranges
from pn.profile.preflop_range import estimate, preflop_range_est
from pn.profile.stats import aggregate, walk_all

FIX = Path(__file__).parent / "fixtures"


def test_no_evidence_returns_chart_width() -> None:
    est = estimate("6max", "CO", "open", (0, 0), [])
    assert abs(est["width_pct"] - est["prior_width_pct"]) < 0.01
    assert est["evidence_hands"] == 0
    chart_core = ranges.parse("QQ+,AKs")
    assert set(chart_core) <= set(ranges.parse(est["range_text"]))


def test_widens_when_shown_hands_fall_outside_chart() -> None:
    base = estimate("6max", "LJ", "open", (0, 0), [])
    wide = estimate("6max", "LJ", "open", (0, 0), ["K5o", "Q7o", "J6s", "T7o"])
    assert wide["width_pct"] > base["width_pct"] + 0.1
    shown = ranges.parse(wide["range_text"])
    assert set(ranges.class_combos("K5o")) <= set(shown)  # shown hands are forced in


def test_in_chart_hands_do_not_widen() -> None:
    base = estimate("6max", "LJ", "open", (0, 0), [])
    same = estimate("6max", "LJ", "open", (0, 0), ["AA", "AKs", "QJs"])
    assert same["width_pct"] <= base["width_pct"] + 0.01


def test_frequency_evidence_moves_width() -> None:
    tight = estimate("6max", "BTN", "open", (10, 100), [])
    loose = estimate("6max", "BTN", "open", (80, 100), [])
    assert tight["width_pct"] < 0.2
    assert loose["width_pct"] > 0.7
    assert tight["ci90"][0] < tight["width_pct"] < tight["ci90"][1]
    assert ranges.width(ranges.parse(tight["range_text"])) < 0.2


def test_profile_block_from_fixtures() -> None:
    hands = json.loads((FIX / "hands_core.json").read_text())
    raw = aggregate(walk_all(hands))["pF"]
    est = preflop_range_est(raw)
    assert est["BB"]["call"]["evidence_hands"] == 3  # QsQd, JcTc, QhQd shown after calling
    assert est["BB"]["3bet"]["evidence_hands"] == 1  # KhQh
    assert est["BB"]["call"]["freq"] == {"n": 16, "successes": 11}
