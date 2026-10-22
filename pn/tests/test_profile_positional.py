"""positional.py (`pn pos`) and the positional profile cells: counts, shrinkage, GTO Wizard mapping."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from test_profile_fixture_build import SIX, build

from pn.profile import gtowizard
from pn.profile.baselines import load_baselines
from pn.profile.build import build_from_walked
from pn.profile.positional import (
    MIN_LEAK_N,
    POSITION_ORDER,
    aggregate_positional,
    build_matrix,
    positional_matrix,
    prior_for,
)
from pn.profile.positional_gto import GtoTable, Spot, gtow_seats, load_table
from pn.profile.priors import load_priors
from pn.profile.stats import aggregate, walk_all

FREQS = gtowizard.freqs_path()
LOG = Path(__file__).resolve().parents[2] / "data/fixtures/pokernow_pglSAMPLE0000000000000000.json"
HERO = "hero000001"
BOARD = "Kd 7s 2c 9h 4d"
HU = [("pV", "BTN"), ("pH", "BB")]
CALL_DOWN = "pV r 125; pH c; F; pH x; pV b 85; pH c; T; pH x; pV x; R; pH x; pV x"

pytestmark = pytest.mark.skipif(not FREQS.exists(), reason="GTO Wizard freqs missing")


@pytest.fixture(scope="module")
def table() -> GtoTable:
    t = load_table()
    assert t is not None
    return t


@pytest.fixture(scope="module")
def spots100() -> dict:
    return gtowizard.spot_table(gtowizard.load_freqs(FREQS), 100)


def _hu_hands(folds: int, calls: int) -> list[dict]:
    hands = [build(f"f{i}", HU, "pV r 125; pH f") for i in range(folds)]
    return hands + [build(f"c{i}", HU, CALL_DOWN, BOARD, winners=["pV"]) for i in range(calls)]


def test_seat_mapping() -> None:
    assert gtow_seats(("UTG", "LJ")) == ("UTG", "HJ")  # collapsed seats move to the next seat
    assert gtow_seats(("BTN_HU", "BB_HU")) == ("SB", "BB")
    assert gtow_seats(("STR",)) is None


def test_lookup_uses_gto_wizard_frequencies(table: GtoTable, spots100: dict) -> None:
    hu = Spot("vs_open", ("BTN_HU", "BB_HU"), "fold", 100)
    assert table.lookup(hu) == pytest.approx(spots100["vs_open|SB|BB"]["fold"])
    bb = Spot("vs_open", ("BTN", "BB"), "raise", 100)
    assert table.lookup(bb) == pytest.approx(spots100["vs_open|BTN|BB"]["raise"])
    if 200 in table.depths():
        deep = Spot("rfi", ("CO",), "raise", 190)
        s200 = gtowizard.spot_table(gtowizard.load_freqs(FREQS), 200)
        assert table.lookup(deep) == pytest.approx(s200["rfi|CO"]["raise"])


def test_heads_up_counts_and_shrinkage(table: GtoTable, spots100: dict) -> None:
    raw = aggregate_positional(_hu_hands(folds=8, calls=2), gto=table)
    hero = raw["pH"]
    assert dict(hero.hands) == {"BB_HU": 10}
    cells = hero.pre["BB_HU"]
    assert (cells["fold_to_steal"].n, cells["fold_to_steal"].s) == (10, 8)
    assert (cells["call_open"].n, cells["call_open"].s) == (10, 2)
    assert (cells["three_bet"].n, cells["three_bet"].s) == (10, 0)
    assert (
        hero.post["BB_HU"]["fold_to_cbet_flop"].n,
        hero.post["BB_HU"]["fold_to_cbet_flop"].s,
    ) == (2, 0)
    assert dict(raw["pV"].hands) == {"BTN_HU": 10} and raw["pV"].pre["BTN_HU"]["rfi"].s == 10

    m = build_matrix(hero, canonical_id="pH")
    assert m["positions"] == ["BB_HU"] and m["hands"] == 10
    obj = m["by_position"]["BB_HU"]["preflop"]["fold_to_steal"]
    assert obj["gto"] == pytest.approx(spots100["vs_open|SB|BB"]["fold"], abs=1e-4)
    # hand-checked: pop prior 0.60, k 16; no BB_HU position prior -> base = pop, k/2 = 8
    assert prior_for(load_priors(), "fold_to_steal") == (0.6, 16.0)
    player = (0.6 * 16 + 8) / (16 + 10)  # 0.6769: pooled player posterior (one position)
    assert obj["post_mean"] == pytest.approx((player * 8 + 8) / (8 + 10), abs=1e-3)  # 0.7453


def test_squeeze_has_no_gto(table: GtoTable) -> None:
    hands = [build(f"s{i}", SIX, "pA r 125; pB c; pC f; pD f; pE f; pF r 500; pA f; pB f")
             for i in range(3)]  # fmt: skip
    raw = aggregate_positional(hands, gto=table)
    bb = raw["pF"].pre["BB"]["three_bet"]
    assert (bb.n, bb.s, bb.gto_n) == (3, 3, 0)
    assert raw["pA"].pre["LJ"]["fold_to_3bet"].gto_n == 0
    clean = raw["pB"].pre["HJ"]["call_open"]  # HJ called a lone open: GTO spot exists
    assert (clean.n, clean.s, clean.gto_n) == (3, 3, 3)


def test_profile_cells_use_gto_wizard_situations(spots100: dict) -> None:
    walked = walk_all(_hu_hands(folds=8, calls=4))
    p = build_from_walked("pH", walked, raw_all=aggregate(walked), priors=load_priors(),
                          baselines=load_baselines())  # fmt: skip
    bp = p["by_position"]
    assert bp["BB_HU"]["three_bet"]["gto"] == pytest.approx(
        spots100["vs_open|SB|BB"]["raise"], abs=1e-4
    )
    assert bp["BB_HU"]["fold_to_steal"]["n"] == 12
    assert bp["OOP"]["fold_to_cbet_flop"]["n"] == 4
    vs = p["by_position_vs_opener"]["BB_HU"]["BTN_HU"]
    assert vs["fold_to_steal"]["gto"] == pytest.approx(spots100["vs_open|SB|BB"]["fold"], abs=1e-4)
    assert all(lk["spot"] != "all" for lk in p["leaks"])  # leaks are positional cells only


def _finite(x: float | None) -> bool:
    return x is None or (math.isfinite(x) and 0 <= x <= 1)


@pytest.mark.skipif(not LOG.exists(), reason="real fixture log missing")
def test_real_log_matrix_is_sane() -> None:
    from pn.ingest.parse import parse_messages, sort_messages

    hands = parse_messages(sort_messages(json.loads(LOG.read_text())), game_id="x")
    m = positional_matrix(HERO, hands)
    assert m["hands"] == 136 and sum(m["hands_by_position"].values()) == 136
    assert set(m["positions"]) <= set(POSITION_ORDER)
    assert {"BTN_HU", "BB_HU"} & set(m["positions"])  # heads-up seats kept apart
    for b in m["by_position"].values():
        cells = [*b["preflop"].values(), *b["postflop"].values()]
        cells += [o for split in b["postflop_split"].values() for o in split.values()]
        for o in cells:
            assert _finite(o["post_mean"]) and _finite(o["gto"]) and o["n"] >= 0
    for lk in m["leaks"]:
        assert lk["n"] >= MIN_LEAK_N and lk["spot"].split(":")[0] in m["positions"]


def test_split_holding_all_hands_equals_position_cell(table: GtoTable) -> None:
    m = positional_matrix("pH", _hu_hands(folds=2, calls=12), gto=table)
    b = m["by_position"]["BB_HU"]
    cell = b["postflop"]["fold_to_cbet_flop"]
    for split in ("oop", "hu"):  # every flop of a heads-up BB is OOP and heads-up
        obj = b["postflop_split"][split]["fold_to_cbet_flop"]
        assert {k: obj[k] for k in ("post_mean", "ci90", "n")} == {
            k: cell[k] for k in ("post_mean", "ci90", "n")
        }  # same hands, same prior; only the GTO baseline (hu / OOP entry) may differ
    assert not any(":" in lk["spot"] for lk in m["leaks"])  # no duplicate split leaks
