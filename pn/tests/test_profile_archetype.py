"""Archetype scoring on synthetic extreme players."""

from __future__ import annotations

import pytest

from pn.profile.archetype import classify
from pn.profile.bayes import beta_binomial
from pn.profile.priors import load_priors

POP = {k: v["mean"] for k, v in load_priors()["stats"].items()}


def _stats(**means: float) -> dict:
    out = {k: {"post_mean": v, "n": 500, "k": 10, "ci90": [v, v], "prior_mean": v}
           for k, v in POP.items()}  # fmt: skip
    for k, v in means.items():
        if k.startswith("af_"):  # aggression factor is a ratio, not a frequency
            out[k] = {"post_mean": v, "n": 1000, "k": 20, "ci90": [v, v], "prior_mean": v}
        else:
            out[k] = beta_binomial(int(v * 1000), 1000, v, 10)
    return out


CASES = {
    "nit": {"vpip": 0.11, "pfr": 0.08},
    "calling_station": {"vpip": 0.52, "pfr": 0.08, "fold_to_cbet_flop": 0.25, "wtsd": 0.42},
    "punching_bag": {"fold_to_cbet_flop": 0.72, "wtsd": 0.18, "af_flop": 0.7, "af_turn": 0.6, "af_river": 0.6},
    "aggressive": {"af_flop": 4.0, "af_turn": 3.8, "af_river": 3.5, "cbet_flop": 0.85, "cbet_turn": 0.75, "three_bet": 0.13},
    "tag": {"vpip": 0.22, "pfr": 0.18},
    "lag": {"vpip": 0.36, "pfr": 0.29},
}  # fmt: skip


@pytest.mark.parametrize("label,means", CASES.items())
def test_extremes(label: str, means: dict) -> None:
    res = classify(_stats(**means), hands=600)
    assert res["label"] == label, res["scores"]
    assert res["scores"][label] >= 0.5
    assert 0 < res["confidence"] <= 1


def test_population_average_is_unknown() -> None:
    assert classify(_stats(), hands=600)["label"] == "unknown"


def test_few_hands_is_unknown() -> None:
    res = classify(_stats(**CASES["nit"]), hands=10)
    assert res["label"] == "unknown"
    assert res["confidence"] < classify(_stats(**CASES["nit"]), hands=600)["confidence"]
