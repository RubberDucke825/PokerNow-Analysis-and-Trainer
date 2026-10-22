"""Beta-Binomial / Dirichlet math and empirical-Bayes fitting."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from pn.profile import bayes


def test_beta_binomial_matches_scipy() -> None:
    m, k, s, n = 0.28, 20.0, 30, 70
    post = bayes.beta_binomial(s, n, m, k)
    a, b = m * k + s, (1 - m) * k + n - s
    assert post["post_mean"] == pytest.approx(a / (a + b), abs=1e-4)
    assert post["ci90"][0] == pytest.approx(stats.beta.ppf(0.05, a, b), abs=1e-4)
    assert post["ci90"][1] == pytest.approx(stats.beta.ppf(0.95, a, b), abs=1e-4)
    assert post["n"] == n and post["k"] == k and post["prior_mean"] == m


def test_contract_formula() -> None:
    post = bayes.beta_binomial(7, 10, 0.5, 10)
    assert post["post_mean"] == pytest.approx((0.5 * 10 + 7) / (10 + 10), abs=1e-4)


def test_no_data_returns_prior() -> None:
    post = bayes.beta_binomial(0, 0, 0.17, 28)
    assert post["post_mean"] == pytest.approx(0.17, abs=1e-4)


def test_large_n_converges_to_empirical() -> None:
    post = bayes.beta_binomial(60_000, 100_000, 0.2, 20)
    assert post["post_mean"] == pytest.approx(0.6, abs=1e-3)
    assert post["ci90"][1] - post["ci90"][0] < 0.01


def test_shrinkage_monotone_in_n() -> None:
    means = [bayes.beta_binomial(int(0.6 * n), n, 0.3, 20)["post_mean"] for n in (5, 50, 500)]
    assert means[0] < means[1] < means[2] < 0.6


def test_bad_counts() -> None:
    with pytest.raises(ValueError):
        bayes.beta_binomial(5, 3, 0.5, 10)


def test_af_posterior() -> None:
    post = bayes.af_posterior(0, 0, 1.8, 20)
    assert post["post_mean"] == pytest.approx(1.8, abs=0.01)
    big = bayes.af_posterior(3000, 4000, 1.0, 20)  # 3000 aggr vs 1000 calls -> AF ~ 3
    assert big["post_mean"] == pytest.approx(3.0, abs=0.05)
    assert big["ci90"][0] < big["post_mean"] < big["ci90"][1]


def test_dirichlet() -> None:
    prior = {"fold": 0.5, "call": 0.4, "raise": 0.1}
    post = bayes.dirichlet({}, prior, 15)
    assert post == pytest.approx(prior)
    post = bayes.dirichlet({"fold": 10}, prior, 10)
    assert post["fold"] == pytest.approx((5 + 10) / 20)
    assert sum(post.values()) == pytest.approx(1.0)
    ci = bayes.dirichlet_marginal_ci({"fold": 10}, prior, 10)
    assert ci["fold"][0] < post["fold"] < ci["fold"][1]


def test_shift_prior() -> None:
    assert bayes.shift_prior(0.4, 0.3, 0.3) == pytest.approx(0.4)
    assert bayes.shift_prior(0.4, 0.5, 0.3) > 0.4


def test_empirical_bayes_recovers_k() -> None:
    rng = np.random.default_rng(7)
    m, k = 0.3, 25.0
    p = rng.beta(m * k, (1 - m) * k, size=400)
    n = rng.integers(50, 300, size=400)
    s = rng.binomial(n, p)
    fit = bayes.fit_beta_binomial(list(zip(s.tolist(), n.tolist(), strict=True)))
    assert fit is not None
    assert fit[0] == pytest.approx(m, abs=0.02)
    assert 15 < fit[1] < 40


def test_empirical_bayes_no_variance_hits_cap() -> None:
    fit = bayes.fit_beta_binomial([(50, 100)] * 20)
    assert fit is not None and fit[1] > 300


def test_empirical_bayes_degenerate() -> None:
    assert bayes.fit_beta_binomial([(1, 2)]) is None
