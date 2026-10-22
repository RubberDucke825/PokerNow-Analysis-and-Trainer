"""pn.train.grading: best / mixed / blunder from the frequency of the chosen action relative
to the most frequent one (contracts/texas_backend.md "Consumers")."""

from __future__ import annotations

import pytest

from pn.train import grading
from pn.train.grading import GradeResult, feedback_fields, grade, try_grade


def test_thresholds_are_relative_to_the_max_frequency() -> None:
    freqs = {"check": 0.6, "bet33": 0.3, "bet75": 0.06, "allin": 0.04}
    assert grade(freqs, "check") == GradeResult("best", 0.6, "check", 0.6)
    assert grade(freqs, "bet33").grade == "best"  # exactly 0.5 x max
    assert grade(freqs, "bet75").grade == "mixed"  # 0.1 x max <= 0.06 < 0.5 x max
    assert grade(freqs, "allin").grade == "blunder"  # below 0.1 x max
    assert grade(freqs, "fold") == GradeResult("blunder", 0.0, "check", 0.6)  # not an option


def test_boundaries_and_constants() -> None:
    assert grading.BEST_SHARE == 0.5 and grading.MIXED_SHARE == 0.1
    assert grade({"a": 1.0, "b": 0.1}, "b").grade == "mixed"  # exactly 0.1 x max
    assert grade({"a": 1.0, "b": 0.0999}, "b").grade == "blunder"
    assert grade({"a": 1.0, "b": 0.4999}, "b").grade == "mixed"


def test_frequencies_are_normalised_and_ties_keep_the_first_action() -> None:
    g = grade({"fold": 30, "call": 70}, "fold")  # GTO Wizard style percentages
    assert g == GradeResult("mixed", 0.3, "call", 0.7)
    tie = grade({"check": 0.5, "bet50": 0.5}, "bet50")
    assert tie.best_token == "check" and tie.grade == "best"
    assert grade({"check": 1.0, "bet50": -0.2}, "check").freq == 1.0  # negatives clamp to 0


def test_pure_spots() -> None:
    assert grade({"fold": 0.0, "call": 1.0}, "call").grade == "best"
    assert grade({"fold": 0.0, "call": 1.0}, "fold").grade == "blunder"


def test_no_frequencies() -> None:
    with pytest.raises(ValueError):
        grade({"check": 0.0, "bet50": 0.0}, "check")
    with pytest.raises(ValueError):
        grade({}, "check")
    assert try_grade(None, "check") is None and try_grade({"a": 0.0}, "a") is None


def test_feedback_fields_share_one_shape() -> None:
    fb = feedback_fields({"check": 0.25, "bet50": 0.75}, "check")
    assert fb == {"grade": "mixed", "freq": 0.25, "best": "bet50", "best_freq": 0.75,
                  "freq_error": 0.75}  # fmt: skip
    assert feedback_fields(None, "check") == {"grade": "none", "freq": None, "best": None,
                                              "best_freq": None, "freq_error": None}  # fmt: skip
