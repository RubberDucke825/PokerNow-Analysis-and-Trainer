"""GTO baselines loading and CI-aware deviations."""

from __future__ import annotations

import pytest

from pn.profile.baselines import annotate, ci_dev, gto_value, load_baselines
from pn.profile.stats import ALL_STATS

BL = load_baselines()


def _stat(lo: float, hi: float, mean: float = 0.5, prior: float = 0.5) -> dict:
    return {"post_mean": mean, "n": 50, "k": 10, "ci90": [lo, hi], "prior_mean": prior}


def test_every_stat_has_a_default_baseline() -> None:
    assert BL["_meta"]["source"] in {"seed", "mixed"}
    for stat in ALL_STATS:
        assert gto_value(BL, stat) is not None, stat


def test_spot_lookup() -> None:
    assert gto_value(BL, "three_bet", "BB") == pytest.approx(0.0882, abs=0.01)  # GTO Wizard
    assert gto_value(BL, "cbet_flop", "hu") == pytest.approx(0.60)
    assert gto_value(BL, "cbet_flop", "multiway") == pytest.approx(0.35)
    assert gto_value(BL, "fold_vs_size", "turn_150") == pytest.approx(0.55)
    assert gto_value(BL, "wsd", "BTN") is None  # no spot value: no fallback to the default
    assert gto_value(BL, "fold_vs_size") is None
    assert gto_value(BL, "nonexistent") is None


@pytest.mark.parametrize(
    "lo,hi,base,expected",
    [
        (0.30, 0.50, 0.40, 0.0),  # inside
        (0.30, 0.50, 0.30, 0.0),  # on the lower edge counts as inside
        (0.30, 0.50, 0.50, 0.0),  # on the upper edge counts as inside
        (0.30, 0.50, 0.20, 0.10),  # baseline below CI: positive, from lower edge
        (0.30, 0.50, 0.65, -0.15),  # baseline above CI: negative, from upper edge
        (0.30, 0.50, None, None),
    ],
)
def test_ci_dev(lo: float, hi: float, base: float | None, expected: float | None) -> None:
    got = ci_dev(_stat(lo, hi), base)
    assert got == (None if expected is None else pytest.approx(expected))


def test_annotate_adds_fields_without_mutating() -> None:
    s = _stat(0.30, 0.50, mean=0.4, prior=0.2)
    out = annotate(s, 0.6)
    assert out["gto"] == 0.6 and out["dev_gto"] == pytest.approx(-0.1)
    assert out["dev_pop"] == pytest.approx(0.1)
    assert "gto" not in s
    assert annotate(s, None)["dev_gto"] is None
