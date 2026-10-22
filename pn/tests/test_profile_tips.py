"""Exploit tips cite n and CI and only fire on confident deviations."""

from __future__ import annotations

import re

from pn.profile.bayes import beta_binomial
from pn.profile.priors import load_priors
from pn.profile.tips import generate_tips

PRIORS = load_priors()
CITE = re.compile(r"n=\d+, CI \d+-\d+")


def _stats(**counts: tuple[int, int]) -> dict:
    out = {}
    for k, v in PRIORS["stats"].items():
        if k.startswith("af_"):
            continue
        s, n = counts.get(k, (0, 0))
        out[k] = beta_binomial(s, n, v["mean"], v["k"])
    return out


def _sizes(**counts: tuple[int, int]) -> dict:
    fvs = PRIORS["fold_vs_size"]
    out: dict = {}
    for key, (s, n) in counts.items():
        street, bucket = key.split("_")
        out.setdefault(street, {})[bucket] = beta_binomial(s, n, fvs[street][bucket], fvs["k"])
    return out


def test_no_data_no_tips() -> None:
    assert generate_tips(_stats(), {}, {}, PRIORS) == []


def test_overfolding_by_size() -> None:
    tips = generate_tips(_stats(), _sizes(turn_150=(40, 45)), {}, PRIORS)
    assert len(tips) == 1
    assert "150% turn bets" in tips[0] and "overbet turns with air" in tips[0]
    assert CITE.search(tips[0])


def test_underfolding_station() -> None:
    tips = generate_tips(_stats(fold_to_cbet_flop=(8, 80), wtsd=(60, 120)), {}, {}, PRIORS)
    text = " | ".join(tips)
    assert "value bet thinner" in text and "no bluffs" in text
    assert all(CITE.search(t) for t in tips)


def test_three_bet_and_limp() -> None:
    tips = generate_tips(_stats(three_bet=(30, 150), limp=(60, 120)), {}, {}, PRIORS)
    text = " | ".join(tips)
    assert "3-bets" in text and "isolation-raise" in text


def test_small_sample_not_tipped() -> None:
    assert generate_tips(_stats(fold_to_cbet_flop=(4, 5)), {}, {}, PRIORS) == []


def test_pairwise_tip() -> None:
    vs = {"pX": {"fold_to_their_bet": beta_binomial(35, 40, 0.45, 15)}}
    tips = generate_tips(_stats(), {}, vs, PRIORS, names={"pX": "hal"})
    assert len(tips) == 1 and tips[0].startswith("vs hal") and CITE.search(tips[0])


def test_passive_af_tip() -> None:
    from pn.profile.bayes import af_posterior

    st = _stats()
    st["af_flop"] = af_posterior(20, 200, 1.8, 20)
    tips = generate_tips(st, {}, {}, PRIORS)
    assert any(t.startswith("Passive on the flop") for t in tips)
