"""pn.train.preflop: situation keys, villain policy (PokerCoaching charts first, GTO Wizard
frequencies for spots they do not cover, hand-written chart fallback), raise sizes, legal
actions and hero grading (pn.train.grading)."""

from __future__ import annotations

import random

import pytest

from pn.profile import gtowizard
from pn.train import preflop as pre
from pn.train.engine import apply, start

SIX = ("LJ", "HJ", "CO", "BTN", "SB", "BB")
EIGHT = ("UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB")


@pytest.fixture(scope="module", autouse=True)
def _needs_freqs() -> None:
    if not gtowizard.freqs_path().exists():
        pytest.skip("GTO Wizard freqs missing")


def _six():
    return start(SIX, 10_000, 50, 100)


def _seq(st, *acts):
    for a in acts:
        st = apply(st, *a) if isinstance(a, tuple) else apply(st, a)
    return st


def test_situation_keys() -> None:
    st = _six()
    assert pre.situation(st, 0).key == "rfi|UTG"  # our 6-max LJ is their UTG
    opened = _seq(st, ("raise", 250), "fold", "fold")
    assert pre.situation(opened, 3).key == "vs_open|UTG|BTN"
    three = _seq(opened, ("raise", 850))  # BTN 3-bets
    assert pre.situation(three, 5).key == "cold|UTG|BTN|BB"
    back = _seq(three, "fold", "fold")
    sit = pre.situation(back, 0)
    assert sit.key == "vs_3bet|UTG|BTN" and sit.exact
    limped = _seq(st, "fold", "fold", "fold", "fold", "call")  # SB completes
    assert pre.situation(limped, 5).key == "vs_limp|SB|BB"
    squeeze = _seq(opened, "call")  # BTN calls: SB faces open + caller
    sit = pre.situation(squeeze, 4)
    assert sit.kind == "vs_open" and sit.callers == 1 and not sit.exact


def _four_bet():
    """6-max: LJ opens, BTN 3-bets, LJ 4-bets; BTN (seat 3) faces the 4-bet."""
    return _seq(_six(), ("raise", 250), "fold", "fold", ("raise", 850), "fold", "fold",
                ("raise", 2000))  # fmt: skip


def test_villain_policy_follows_pokercoaching_charts() -> None:
    st = _six()
    rng = random.Random(1)
    kind, to, origin = pre.villain_action(st, 0, "AsAh", rng=rng, table="6max", depth=100)
    assert (kind, to, origin) == ("raise", 250, "pc:open LJ")  # GTOW still sizes: 2.5bb
    assert pre.villain_action(st, 0, "7c2d", rng=rng, table="6max", depth=100)[0] == "fold"
    # pure chart: 76s from LJ is always the same action (GTO Wizard mixed it at 37%)
    acts = {pre.villain_action(st, 0, "7s6s", rng=rng, table="6max", depth=100)[0]
            for _ in range(200)}  # fmt: skip
    assert len(acts) == 1
    # BTN vs LJ open: AA 3-bets (PokerCoaching) to the GTO Wizard size 8.5bb.
    opened = _seq(st, ("raise", 250), "fold", "fold")
    kind, to, origin = pre.villain_action(opened, 3, "AdAc", rng=rng, table="6max", depth=100)
    assert (kind, to, origin) == ("raise", 850, "pc:3bet BTN vs LJ")


def test_villain_policy_falls_back_to_gto_wizard_where_pokercoaching_has_no_chart() -> None:
    st = _four_bet()
    sit = pre.situation(st, 3)
    assert sit.key == "vs_4bet|UTG|BTN" and pre.pc_decision(st, 3, sit, "7s6s", "6max") is None
    rng = random.Random(1)
    origin = pre.villain_action(st, 3, "7s6s", rng=rng, table="6max", depth=100)[2]
    assert origin == "gtow:100|vs_4bet|UTG|BTN"
    # 76s calls the 4-bet 53% in GTO Wizard: the sampled share converges.
    calls = sum(pre.villain_action(st, 3, "7s6s", rng=rng, table="6max", depth=100)[0] == "call"
                for _ in range(2000))  # fmt: skip
    assert calls / 2000 == pytest.approx(0.53, abs=0.04)


def test_chart_fallback_without_a_pokercoaching_or_gto_wizard_spot() -> None:
    st = _seq(start(EIGHT, 10_000, 50, 100), "call")  # UTG limps: UTG1 faces a limper
    sit = pre.situation(st, 1)
    assert sit.kind is None and sit.key is None
    kind, to, origin = pre.villain_action(st, 1, "AsAh", rng=random.Random(0), table="8max", depth=100)
    assert origin == "chart" and kind == "raise" and to == 350  # 2.5bb + 1bb per limper
    assert pre.villain_action(st, 1, "7c2d", rng=random.Random(0), table="8max", depth=100)[0] == "fold"


def test_eight_max_utg_opens_from_pokercoaching() -> None:
    st = start(EIGHT, 10_000, 50, 100)  # 8-max UTG: no GTO Wizard seat; PC 9-handed UTG1
    kind, to, origin = pre.villain_action(st, 0, "AsAh", rng=random.Random(0), table="8max", depth=100)
    assert (kind, to, origin) == ("raise", 250, "pc:open UTG1")


def test_raise_sizes_fall_back_to_the_formula() -> None:
    st = _six()
    limp = _seq(st, "call")  # LJ limps: HJ isolates 2.5 + 1 bb
    assert pre.raise_to(limp, 1, pre.situation(limp, 1), None) == 350
    opened = _seq(st, ("raise", 250), "call")  # HJ calls: CO squeezes 3x IP + 1x
    sit = pre.situation(opened, 2)
    assert pre.raise_to(opened, 2, sit, {"raise_to_bb": 8.5}) == 1000  # not exact: formula
    sb = _seq(st, ("raise", 250), "fold", "fold", "fold")
    assert pre.raise_to(sb, 4, pre.situation(sb, 4), None) == 875  # 3.5x OOP


def test_legal_actions_preflop() -> None:
    st = _six()
    toks = [o["token"] for o in pre.legal_actions(st, 0, 250)]
    assert toks == ["fold", "call", "raise", "allin"]
    bb = _seq(st, "fold", "fold", "fold", "fold", "call")
    assert [o["token"] for o in pre.legal_actions(bb, 5, 350)] == ["check", "raise", "allin"]


def test_hero_grade_uses_the_shared_frequency_grade() -> None:
    st = _seq(_six(), "fold", "fold")  # folds to the BTN: PokerCoaching opens 51.1%
    g = pre.hero_grade(st, 3, "7s2h", "raise", "6max", 100)
    freq = {a["token"]: a["freq"] for a in g["actions"]}
    assert freq == {"fold": 1.0, "call": 0.0, "raise": 0.0, "allin": 0.0}  # pure chart
    assert (g["grade"], g["best"], g["freq"], g["best_freq"]) == ("blunder", "fold", 0.0, 1.0)
    assert g["preflop_source"] == "pokercoaching" and g["origin"] == "pc:open BTN"
    assert g["freq_error"] == 1.0 and g["loss_bb"] is None
    assert all(a["ev_bb"] is None for a in g["actions"])
    a2s = pre.hero_grade(st, 3, "As2s", "raise", "6max", 100)
    assert (a2s["grade"], a2s["best"], a2s["freq"]) == ("best", "raise", 1.0)
    assert pre.hero_grade(st, 3, "As2s", "fold", "6max", 100)["grade"] == "blunder"


def test_sb_first_in_raises_limps_or_folds_from_pokercoaching() -> None:
    sb = _seq(_six(), "fold", "fold", "fold", "fold")
    aces = pre.hero_grade(sb, 4, "AsAh", "call", "6max", 100)  # AA limps in the SB chart
    assert aces["grade"] == "best" and aces["best"] == "call" and aces["situation"] == "rfi|SB"
    assert pre.hero_grade(sb, 4, "AsAh", "fold", "6max", 100)["grade"] == "blunder"
    # bots never limp (2026-10-16): the SB's limp-reraise AA opens instead
    assert pre.villain_action(sb, 4, "AsAh", rng=random.Random(0), table="6max",
                              depth=100)[0] == "raise"  # fmt: skip
    trash = pre.hero_grade(sb, 4, "7s2h", "fold", "6max", 100)
    assert trash["grade"] == "best" and trash["preflop_source"] == "pokercoaching"
    raised = [c for c in ("AsKh", "AsQs", "KsQs", "9s8s", "5s5h", "Ts9s", "Js4s")
              if pre.hero_grade(sb, 4, c, "raise", "6max", 100)["grade"] == "best"]
    assert raised  # the raise range (`open SB`) is used too


def test_bb_vs_sb_limp_uses_gto_wizard_and_sb_vs_iso_uses_pokercoaching() -> None:
    limped = _seq(_six(), "fold", "fold", "fold", "fold", "call")
    g = pre.hero_grade(limped, 5, "AsAh", "check", "6max", 100)
    assert g["preflop_source"] == "gtowizard" and g["origin"] == "gtow:100|vs_limp|SB|BB"
    assert g["grade"] == "blunder" and g["best"] == "raise"
    iso = _seq(limped, ("raise", 400))  # BB raises the limp: SB limp vs raise
    sit = pre.situation(iso, 4)
    assert sit.kind == "limp_vs_raise" and sit.key is None
    g = pre.hero_grade(iso, 4, "AsAh", "raise", "6max", 100)
    assert g["preflop_source"] == "pokercoaching" and g["grade"] == "best"
    assert g["origin"] == "pc:3bet SB limp_vs_raise BB"


def test_vs_four_bet_grades_from_gto_wizard_frequencies() -> None:
    st = _four_bet()
    g = pre.hero_grade(st, 3, "AsAh", "call", "6max", 100)  # AA: all-in 11%, call 89%
    assert g["preflop_source"] == "gtowizard" and g["origin"] == "gtow:100|vs_4bet|UTG|BTN"
    freq = {a["token"]: a["freq"] for a in g["actions"]}
    assert freq["call"] == pytest.approx(0.89) and freq["allin"] == pytest.approx(0.11)
    assert g["grade"] == "best" and g["best"] == "call"
    assert pre.hero_grade(st, 3, "AsAh", "fold", "6max", 100)["grade"] == "blunder"


# ---- heads-up bots (2026-10-16): no limps, no cold calls into 3+, no squeezes ----
def _kinds(st, i, combo, n=300, table="6max"):
    rng = random.Random(7)
    return {pre.villain_action(st, i, combo, rng=rng, table=table, depth=100)[0] for _ in range(n)}


def test_bots_never_limp_sb_included() -> None:
    folded_to_sb = _seq(_six(), "fold", "fold", "fold", "fold")  # SB first in
    for combo in ("7s6s", "Kc5d", "Td4c", "AsAh", "9h3h"):
        assert "call" not in _kinds(folded_to_sb, 4, combo), combo
    first_in = _six()
    for combo in ("7s6s", "5c5d", "KdJd"):
        assert "call" not in _kinds(first_in, 0, combo), combo


def test_bots_call_heads_up_but_never_cold_call_into_three() -> None:
    opened = _seq(_six(), ("raise", 250), "fold", "fold")  # LJ opens, BTN to act
    # one raiser in: a flat keeps it heads-up (players behind will fold), charts unchanged
    assert pre.villain_action(opened, 3, "AdAc", rng=random.Random(1), table="6max", depth=100)[0] == "raise"
    flatted = _seq(opened, "call")  # BTN flats: SB faces open + caller
    for combo in ("7s6s", "AsAh", "QdJd", "5c5d", "AhKh"):
        assert _kinds(flatted, 4, combo) <= {"fold"}, combo  # no overcall, no squeeze
    bb = _seq(flatted, "fold")
    for combo in ("9s8s", "AsAh", "KcQc"):
        assert _kinds(bb, 5, combo) <= {"fold"}, combo


def test_bots_still_three_and_four_bet_a_single_raiser() -> None:
    four = _four_bet()  # LJ opened, BTN 3-bet, LJ 4-bet; BTN faces it heads-up
    assert _kinds(four, 3, "7s6s", n=2000) == {"call", "fold"}  # GTO Wizard mix still sampled
    three = _seq(_six(), ("raise", 250), "fold", "fold", ("raise", 850))
    # SB facing open + 3-bet: a cold 4-bet or cold call would bring a third player
    for combo in ("AsAh", "KsKh", "AhKh"):
        assert _kinds(three, 4, combo) <= {"fold"}, combo


def test_hero_grading_unchanged_by_the_bot_filter() -> None:
    flatted = _seq(_six(), ("raise", 250), "fold", "fold", "call")
    g = pre.hero_grade(flatted, 4, "AsAh", "raise", "6max", 100)
    assert any(a["token"] == "raise" and a["freq"] for a in g["actions"])  # hero may squeeze


def test_sb_chart_limps_become_raise_or_fold_for_bots() -> None:
    sb = _seq(_six(), "fold", "fold", "fold", "fold")
    assert _kinds(sb, 4, "AsAh") == {"raise"}  # 3bet_value limp_vs_raise: trap -> open
    assert _kinds(sb, 4, "As5s") == {"raise"}  # call limp_vs_raise: playable -> open
    assert _kinds(sb, 4, "Ks5d") == {"fold"}  # 3bet_bluff limp_vs_raise -> fold
