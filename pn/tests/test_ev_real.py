"""Integration: grade a river decision of the synthetic heads-up hand with the real TexasSolver
binary. Skipped when console_solver is not built."""

from __future__ import annotations

import pytest

from pn.ev.decisions import enumerate_decisions
from pn.ev.grade import Grader
from pn.solve import SolveCache, texas
from tests.test_ev_grade import HERO, _hand

REAL_CMD = texas.texas_cmd()  # before conftest's autouse fixture hides it
if REAL_CMD is None:
    pytest.skip("console_solver not built", allow_module_level=True)


def _river_hand() -> dict:
    hand = _hand()
    hand["board"]["river"] = "9s"
    hand["players"][1]["cards"] = ["8c", "7c"]  # in the BB line range at the river (AKo 3-bets pre)
    n = len(hand["actions"])
    hand["actions"] += [
        {"street": "river", "seq": n, "player_id": HERO, "kind": "bet", "amount": 1000,
         "to_amount": 1000, "allin": False},
        {"street": "river", "seq": n + 1, "player_id": "vB", "kind": "fold", "amount": 0,
         "to_amount": 0, "allin": False},
    ]  # fmt: skip
    return hand


def test_real_solver_grades_river_bet(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(texas, "texas_cmd", lambda: REAL_CMD)
    hand = _river_hand()
    decs = [d for d in enumerate_decisions(hand, {HERO}) if d.street == "river"]
    grader = Grader(SolveCache(tmp_path), source="gto", profiles={},
                    game={"max_iterations": 50, "time_limit_sec": 20})  # fmt: skip
    (row,) = grader.grade_hand(hand, decs)
    assert row.grade in ("best", "mixed", "blunder"), row.note  # Plan 7: frequency grade
    assert 0 <= row.freq_chosen <= row.best_freq <= 1
    assert row.chosen.startswith(("bet", "allin")) and row.best and row.backend
