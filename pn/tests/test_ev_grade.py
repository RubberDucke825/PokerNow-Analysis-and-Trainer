"""pn.ev decisions + grading on a synthetic heads-up hand, solver replaced by hand-built docs.

Hand: BTN (villain vB) opens to 750, BB (hero hB, AsKc) calls. Flop Kd Ah 5h, pot 1500: hero
bets 500 (snaps to bet33 = 495), villain calls. Turn 2c: hero checks, villain checks. River
none (villain shows QcQs at an imaginary showdown so both players have known cards).
"""

from __future__ import annotations

import pytest

from pn import ranges
from pn.ev.decisions import enumerate_decisions, raw_token
from pn.ev.grade import Grader, gto_freq, spot_note, with_combo
from pn.profile.walk import _walk_decisions
from pn.solve import SolveCache, SolveError

HERO, VILLAIN = "hB", "vB"
HERO_COMBO = ranges.canonical_combo("AsKc")


def _hand(flags: list[str] | None = None) -> dict:
    acts = [("preflop", VILLAIN, "post_sb", 25, 25), ("preflop", HERO, "post_bb", 50, 50),
            ("preflop", VILLAIN, "raise", 725, 750), ("preflop", HERO, "call", 700, 750),
            ("flop", HERO, "bet", 500, 500), ("flop", VILLAIN, "call", 500, 500),
            ("turn", HERO, "check", 0, 0), ("turn", VILLAIN, "check", 0, 0)]  # fmt: skip
    return {
        "hand_id": "ev1", "hand_no": 7, "flags": flags or [],
        "blinds": {"sb": 25, "bb": 50, "ante": 0, "straddle": 0},
        "players": [
            {"player_id": VILLAIN, "name": "hal", "position": "BTN", "stack_start": 15450,
             "cards": None},
            {"player_id": HERO, "name": "hero", "position": "BB", "stack_start": 10550,
             "cards": ["As", "Kc"], "cards_source": "hero_export"},
        ],
        "actions": [{"street": st, "seq": i, "player_id": pid, "kind": k, "amount": a,
                     "to_amount": to, "allin": False} for i, (st, pid, k, a, to) in enumerate(acts)],
        "board": {"flop": ["Kd", "Ah", "5h"], "turn": "2c", "river": None},
        "showdowns": [{"player_id": VILLAIN, "cards": ["Qc", "Qs"], "shown": True}],
    }  # fmt: skip


def _doc(freqs: dict[str, list[float]], n_players: int = 2) -> dict:
    """One action node for the first actor: check / bet33 with per-combo strategy rows; a
    TexasSolver-shaped document (no EVs)."""
    combos = list(ranges.all_combos())
    table = [freqs.get(c, [0.0, 0.0]) for c in combos]
    return {"meta": {"backend": "texas", "ev_available": False}, "players": [], "combos": combos,
            "root": "n0",
            "nodes": {"n0": {"type": "action", "player": 0, "street": "flop", "history": [],
                             "actions": [{"token": "check", "child": "n1"},
                                         {"token": "bet33", "amount": 495, "child": "n2"}],
                             "strategy": table,
                             "reach_by_player": [[1.0] * len(combos)] * n_players},
                      "n1": {"type": "terminal", "kind": "leaf", "pot": 0},
                      "n2": {"type": "terminal", "kind": "leaf", "pot": 0}}}  # fmt: skip


class Runner:
    def __init__(self, doc: dict | None = None, fail: bool = False) -> None:
        self.doc, self.fail, self.spots = doc, fail, []

    def __call__(self, spot: dict, backend: str | None) -> tuple[dict, str]:
        self.spots.append(spot)
        if self.fail:
            raise SolveError("boom", 1)
        return self.doc, "texas"


FREQS = {f"{d}|vs_open|SB|BB": {"cells": {"AKo": {"R": 30, "C": 70}}} for d in (100, 200)}


def _grader(tmp_path, runner: Runner, source: str = "gto") -> Grader:
    return Grader(SolveCache(tmp_path, runner=runner), source=source, profiles={},
                  freqs=FREQS)  # fmt: skip


# ------------------------------------------------------------------ decisions


def test_enumerates_known_card_players_only_voluntary() -> None:
    decs = enumerate_decisions(_hand())
    assert [(d.seq, d.player_id, d.street, d.token, d.spot) for d in decs] == [
        (2, VILLAIN, "preflop", "raise1500", "pf|BTN|rfi|-"),
        (3, HERO, "preflop", "call", "pf|BB|vs_open|BTN"),
        (4, HERO, "flop", "bet33", "post|srp|oop|flop|first"),
        (5, VILLAIN, "flop", "call", "post|srp|ip|flop|facing_bet"),
        (6, HERO, "turn", "check", "post|srp|oop|turn|first"),
        (7, VILLAIN, "turn", "check", "post|srp|ip|turn|first"),
    ]
    assert decs[1].situ == ("vs_open", "BTN_HU", "BB_HU") and decs[1].combo == HERO_COMBO
    assert decs[0].combo == ranges.canonical_combo("QcQs")
    assert [d.seq for d in enumerate_decisions(_hand(), {HERO})] == [3, 4, 6]


def test_no_known_cards_no_decisions() -> None:
    hand = _hand()
    hand["players"][1]["cards"] = None
    hand["showdowns"] = []
    assert enumerate_decisions(hand) == []


def test_raw_token_allin() -> None:
    hand = _hand()
    hand["actions"][4]["allin"] = True
    assert enumerate_decisions(hand, {HERO})[1].token == "allin"
    assert [raw_token(d) for d in _walk_decisions(_hand())][:2] == ["raise1500", "call"]


# ------------------------------------------------------------------ frequency grading


def test_grading_is_by_frequency(tmp_path) -> None:
    runner = Runner(_doc({HERO_COMBO: [0.8, 0.2]}))  # check 80%, bet33 20%
    rows = _grader(tmp_path, runner).grade_hand(_hand(), enumerate_decisions(_hand(), {HERO}))
    pre, flop, turn = rows
    assert (flop.chosen, flop.best, flop.grade, flop.freq_chosen, flop.best_freq) == (
        "bet33", "check", "mixed", 0.2, 0.8)  # 0.2 >= 0.1 x 0.8 but < 0.5 x 0.8
    assert (flop.ev_chosen, flop.ev_best, flop.loss) == (None, None, None)  # texas: no EVs
    assert flop.backend == "texas" and flop.solve_key and flop.source == "gto"
    hero_range = ranges.parse(runner.spots[0]["players"][0]["range"])
    assert hero_range[HERO_COMBO] > 0  # always solvable for the actual combo
    assert flop.note == (None if hero_range[HERO_COMBO] > 0.001 else "combo_added_to_range")
    assert (turn.chosen, turn.best, turn.grade) == ("check", "check", "best")
    assert len(runner.spots) == 2  # one solve per postflop street, preflop never solved
    assert not any("lock" in p for s in runner.spots for p in s["players"])  # GTO, no locks
    # preflop: chart-based, GTO Wizard class frequencies (AKo calls 70% vs the SB open)
    assert (pre.chosen, pre.best, pre.grade, pre.note) == ("call", "call", "best", None)
    assert pre.freq_chosen == pytest.approx(0.7) and pre.gto_freq_chosen == pytest.approx(0.7)
    blunder = Runner(_doc({HERO_COMBO: [0.97, 0.03]}))
    rows = _grader(tmp_path / "b", blunder).grade_hand(_hand(), enumerate_decisions(_hand(), {HERO}))
    assert rows[1].grade == "blunder" and rows[1].freq_chosen == 0.03


def test_documents_with_evs_still_fill_the_ev_columns(tmp_path) -> None:
    doc = _doc({HERO_COMBO: [0.8, 0.2]})
    doc["meta"] = {"backend": "texas", "ev_available": True, "exploitability_pct_pot": 1.5}
    doc["nodes"]["n0"]["action_ev"] = [[100.0, 40.0] if c == HERO_COMBO else [0.0, 0.0]
                                       for c in doc["combos"]]  # fmt: skip
    runner = Runner(doc)
    rows = _grader(tmp_path, runner).grade_hand(_hand(), enumerate_decisions(_hand(), {HERO}))
    spot = runner.spots[0]  # heads-up flop: review preset (cap 2, 120 s)
    assert spot["sizes"]["max_bets_per_street"] == 2 and spot["game"]["time_limit_sec"] == 120
    flop = rows[1]
    assert (flop.ev_chosen, flop.ev_best, flop.loss, flop.grade) == (40, 100, 60, "mixed")
    assert flop.exploitability == 1.5


def test_combo_outside_range_is_added_and_noted(tmp_path) -> None:
    cfg = {"players": [{"range": "QQ"}, {"range": "AA"}]}
    out, added = with_combo(cfg, 0, HERO_COMBO)
    assert added and ranges.parse(out["players"][0]["range"])[HERO_COMBO] == pytest.approx(0.001)
    assert cfg["players"][0]["range"] == "QQ"  # input untouched
    assert with_combo(out, 0, HERO_COMBO)[1] is False


def test_null_rows_carry_notes(tmp_path) -> None:
    decs = enumerate_decisions(_hand(), {HERO})
    rows = _grader(tmp_path, Runner(fail=True)).grade_hand(_hand(), decs)
    assert rows[0].grade == "best"  # preflop needs no solve
    assert all(r.grade is None and r.loss is None for r in rows[1:])
    assert rows[1].note.startswith("solve_failed") and rows[1].best == ""
    inc = _grader(tmp_path, Runner(_doc({}))).grade_hand(_hand(["incomplete"]), decs)
    assert {r.note for r in inc} == {"incomplete_hand"}
    no_cell = Grader(SolveCache(tmp_path / "c", runner=Runner(_doc({}))), freqs={})
    assert no_cell.grade_hand(_hand(), decs)[0].note == "no_preflop_frequencies"
    with pytest.raises(ValueError, match="source"):
        Grader(SolveCache(tmp_path), source="tendencies")


def test_off_tree_and_missing_strategy(tmp_path) -> None:
    doc = _doc({})
    node = doc["nodes"]["n0"]
    node["actions"] = [{"token": "check", "child": "n1"}]  # no bet: off tree
    node["strategy"] = [[1.0] for _ in doc["combos"]]
    rows = _grader(tmp_path, Runner(doc)).grade_hand(_hand(), enumerate_decisions(_hand(), {HERO}))
    assert rows[1].note.startswith("off_tree") and rows[2].grade == "best"
    del node["strategy"]
    rows = _grader(tmp_path / "b", Runner(doc)).grade_hand(_hand(), enumerate_decisions(_hand(), {HERO}))
    assert rows[2].note.startswith("no_strategy") and rows[2].grade is None


def test_spot_note_and_gto_freq_edges() -> None:
    assert spot_note(ValueError("9 players: the tree supports at most 8")) == "too_many_players"
    assert spot_note(ValueError("x all-in before the turn: ...")) == "allin_before_street"
    assert spot_note(ValueError("hand h did not reach the river")) == "incomplete_hand"
    dec = enumerate_decisions(_hand(), {HERO})[0]
    assert gto_freq(None, _hand(), dec) is None
    assert gto_freq({}, _hand(), dec) is None


def _three_way_hand() -> dict:
    """BTN opens, SB and hero (BB) call; flop: SB checks, hero bets, BTN folds, SB calls (3 see
    the flop); turn heads-up: SB checks, hero checks."""
    sb = "sS"
    acts = [("preflop", sb, "post_sb", 25, 25), ("preflop", HERO, "post_bb", 50, 50),
            ("preflop", VILLAIN, "raise", 750, 750), ("preflop", sb, "call", 725, 750),
            ("preflop", HERO, "call", 700, 750),
            ("flop", sb, "check", 0, 0), ("flop", HERO, "bet", 500, 500),
            ("flop", VILLAIN, "fold", 0, 0), ("flop", sb, "call", 500, 500),
            ("turn", sb, "check", 0, 0), ("turn", HERO, "check", 0, 0)]  # fmt: skip
    hand = _hand()
    hand["players"] = [
        {"player_id": VILLAIN, "name": "hal", "position": "BTN", "stack_start": 15450,
         "cards": None},
        {"player_id": sb, "name": "sam", "position": "SB", "stack_start": 12000, "cards": None},
        {"player_id": HERO, "name": "hero", "position": "BB", "stack_start": 10550,
         "cards": ["As", "Kc"], "cards_source": "hero_export"},
    ]  # fmt: skip
    hand["actions"] = [{"street": st, "seq": i, "player_id": pid, "kind": k, "amount": a,
                        "to_amount": to, "allin": False}
                       for i, (st, pid, k, a, to) in enumerate(acts)]  # fmt: skip
    return hand


def test_multiway_street_rows_are_unsolvable(tmp_path) -> None:
    hand = _three_way_hand()
    runner = Runner(_doc({HERO_COMBO: [100.0, 40.0]}))
    rows = _grader(tmp_path, runner).grade_hand(hand, enumerate_decisions(hand, {HERO}))
    flop = [r for r in rows if r.street == "flop"]
    assert flop and all(r.note == "multiway_unsolvable" and r.loss is None and r.ev_best is None
                        for r in flop)  # fmt: skip
    turn = [r for r in rows if r.street == "turn"]  # heads-up again, still not graded
    assert turn and all(r.note == "multiway_unsolvable" and r.loss is None for r in turn)
    assert runner.spots == []  # no postflop street of a multiway-flop hand is solved


# ------------------------------------------------------------------ Plan 7 T6: PokerCoaching first

SIX = ("LJ", "HJ", "CO", "BTN", "SB", "BB")


def _six_hand(hero: str, cards: list[str], acts: list[tuple]) -> dict:
    """6-max, 100bb (5000 at 25/50), ends preflop. `acts` = (position, kind, to_amount)."""
    pid = {p: f"p{p}" for p in SIX}
    pid[hero] = HERO
    level, put = 50, {"SB": 25, "BB": 50}
    rows = [("SB", "post_sb", 25, 25), ("BB", "post_bb", 50, 50)]
    for pos, kind, to in acts:
        to = to if kind == "raise" else level if kind == "call" else put.get(pos, 0)
        rows.append((pos, kind, to - put.get(pos, 0), to))
        put[pos], level = to, max(level, to)
    return {
        "hand_id": "pc1", "hand_no": 1, "flags": [],
        "blinds": {"sb": 25, "bb": 50, "ante": 0, "straddle": 0},
        "players": [{"player_id": pid[p], "name": p.lower(), "position": p, "stack_start": 5000,
                     "cards": cards if p == hero else None,
                     **({"cards_source": "hero_export"} if p == hero else {})} for p in SIX],
        "actions": [{"street": "preflop", "seq": i, "player_id": pid[pos], "kind": k, "amount": a,
                     "to_amount": to, "allin": False} for i, (pos, k, a, to) in enumerate(rows)],
        "board": {"flop": None, "turn": None, "river": None}, "showdowns": [],
    }  # fmt: skip


def _pre_rows(tmp_path, hand: dict, freqs: dict | None = None) -> list:
    grader = Grader(SolveCache(tmp_path, runner=Runner(fail=True)), freqs=freqs or {})
    return grader.grade_hand(hand, enumerate_decisions(hand, {HERO}))


FOLD_TO_BTN = [("LJ", "fold", 0), ("HJ", "fold", 0), ("CO", "fold", 0)]


def test_review_preflop_grades_from_pokercoaching_first(tmp_path) -> None:
    # GTO Wizard says raise 72o: PokerCoaching (BTN opens 51.1%) wins and folds it.
    gtow = {"100|rfi|BTN": {"cells": {"72o": {"R": 100}}}}
    trash = _six_hand("BTN", ["7s", "2h"], [*FOLD_TO_BTN, ("BTN", "raise", 125),
                                            ("SB", "fold", 0), ("BB", "fold", 0)])  # fmt: skip
    (row,) = _pre_rows(tmp_path / "a", trash, gtow)
    assert (row.grade, row.best, row.freq_chosen, row.backend) == ("blunder", "fold", 0.0,
                                                                   "pokercoaching")  # fmt: skip
    a2s = _six_hand("BTN", ["As", "2s"], [*FOLD_TO_BTN, ("BTN", "raise", 125),
                                          ("SB", "fold", 0), ("BB", "fold", 0)])  # fmt: skip
    (row,) = _pre_rows(tmp_path / "b", a2s)
    assert (row.grade, row.best, row.freq_chosen) == ("best", "raise", 1.0)


def test_review_sb_limps_aces_and_limp_vs_raise_is_charted(tmp_path) -> None:
    hand = _six_hand("SB", ["As", "Ah"], [*FOLD_TO_BTN, ("BTN", "fold", 0), ("SB", "call", 0),
                                          ("BB", "raise", 200), ("SB", "call", 0)])  # fmt: skip
    limp, vs_raise = _pre_rows(tmp_path, hand)
    assert (limp.grade, limp.best, limp.backend) == ("best", "call", "pokercoaching")
    assert vs_raise.backend == "pokercoaching" and vs_raise.best == "raise"  # AA limp-reraises
    assert vs_raise.grade == "blunder"


def test_review_vs_four_bet_falls_back_to_gto_wizard(tmp_path) -> None:
    gtow = {"100|vs_4bet|UTG|BTN": {"cells": {"AA": {"A": 11, "C": 89}}}}
    hand = _six_hand("BTN", ["As", "Ah"], [("LJ", "raise", 125), ("HJ", "fold", 0),
                                           ("CO", "fold", 0), ("BTN", "raise", 400),
                                           ("SB", "fold", 0), ("BB", "fold", 0),
                                           ("LJ", "raise", 1000), ("BTN", "call", 0)])  # fmt: skip
    three, call = _pre_rows(tmp_path, hand, gtow)
    assert (three.grade, three.backend) == ("best", "pokercoaching")  # 3bet BTN vs LJ
    assert (call.grade, call.backend, call.best) == ("best", "gtowizard", "call")
    assert call.freq_chosen == pytest.approx(0.89)
    assert _pre_rows(tmp_path / "x", hand)[1].note == "no_preflop_frequencies"
