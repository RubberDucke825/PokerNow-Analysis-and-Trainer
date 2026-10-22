"""Plan 14 P2: spot importance = freq x pot x (1 - score)^p normalised to the top row ("Spot
importance - V3"), score = shrunk trainer accuracy; formations, textures, drill weights."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

from pn.train import importance as imp
from pn.train import textures as tx
from pn.train.leaks import Leaks, Line, Step

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))


def _row(spot: str | None, grade: str = "best", *, street: str | None = None, board: str | None = None,
         pot_bb: float | None = None, formation: str | None = None) -> dict:  # fmt: skip
    street = street or ("preflop" if spot is None or spot.startswith("pf|") else spot.split("|")[3])
    return {"hand_uid": "h", "spot": spot, "chosen": "check", "grade": grade, "options_json": "[]",
            "note": None, "drill_spot": None, "street": street, "board": board, "pot_bb": pot_bb,
            "formation": formation}  # fmt: skip


# ------------------------------------------------------------------ the formula


def test_priority_is_freq_pot_and_weakness_to_the_p_normalised_to_the_max() -> None:
    items = [{"freq": 0.04079, "pot_bb": 6.0, "score": 0.7}, {"freq": 0.04936, "pot_bb": 5.5, "score": 0.74},
             {"freq": 0.00729, "pot_bb": 24.0, "score": 0.7}]  # the sheet's first Formations rows  # fmt: skip
    out = imp.prioritise(items, 3)
    raw = [i["freq"] * i["pot_bb"] * (1 - i["score"]) ** 3 for i in items]
    assert [r["priority"] for r in out] == pytest.approx([x / max(raw) for x in raw], abs=1e-5)
    assert out[0]["priority"] == 1.0
    assert out[1]["priority"] == pytest.approx(0.7220863986, abs=1e-5)  # the sheet's value
    assert out[2]["priority"] == pytest.approx(0.7148810983, abs=1e-5)
    assert items[0].get("priority") is None  # inputs are not mutated
    # p = 1 takes the score at face value; a perfect score everywhere prices every row at 0
    assert imp.prioritise([{"freq": 0.1, "pot_bb": 5, "score": 1.0}], 1)[0]["priority"] == 0.0
    assert imp.prioritise([], 3) == []


def test_score_is_accuracy_shrunk_toward_the_global_score() -> None:
    assert imp.accuracy(_row("pf|CO|rfi|-", "best")) == 1.0
    assert imp.accuracy(_row("pf|CO|rfi|-", "mixed")) == 0.5
    assert imp.accuracy(_row("pf|CO|rfi|-", "blunder")) == 0.0
    assert imp.accuracy(_row("pf|CO|rfi|-", "none")) is None
    rows = (
        [_row("pf|CO|rfi|-", "blunder")] * 3
        + [_row("pf|BTN|rfi|-", "mixed")] * 2
        + [_row("pf|BTN|rfi|-", "none")]
    )
    g = imp.global_score(rows)
    assert g == pytest.approx((0 + 0.5 * 2 + imp.PRIOR_N * imp.PRIOR_SCORE) / (5 + imp.PRIOR_N))
    assert imp.global_score([]) == imp.PRIOR_SCORE
    assert imp.shrunk(0.0, 3, g) == pytest.approx((0 + imp.PRIOR_N * g) / (3 + imp.PRIOR_N))
    assert imp.shrunk(0.0, 0, g) == g  # no data: the global score


@pytest.mark.parametrize(("p_form", "p_tex", "pot"), [(0, 1, "start"), (6, 1, "start"), (3, 0, "avg"),
                                                     (3, 6, "avg"), (3, 1, "mean"), (2.5, 1, "start")])  # fmt: skip
def test_params_out_of_range(p_form, p_tex, pot) -> None:
    with pytest.raises(ValueError):
        imp.check_params(p_form, p_tex, pot)


def test_params_in_range() -> None:
    for p in range(1, 6):
        imp.check_params(p, p, "start")
        imp.check_params(p, p, "avg")


def test_drill_weights_are_priority_plus_exploration_normalised() -> None:
    items = [{"priority": 1.0, "n": 10}, {"priority": 0.2, "n": 0}, {"priority": 0.0, "n": 2}]
    out = imp.with_weights(items)
    big_n = 12
    raw = [
        it["priority"] + imp.C_EXPLORE * math.sqrt(math.log(big_n) / (it["n"] + 1)) for it in items
    ]
    assert [r["weight"] for r in out] == pytest.approx([x / sum(raw) for x in raw], abs=1e-5)
    assert sum(r["weight"] for r in out) == pytest.approx(1.0, abs=1e-4)
    assert out[2]["weight"] > 0  # never-good spots with priority 0 still get dealt
    fresh = imp.with_weights([{"priority": 0.5, "n": 0}])  # N = max(0, 2)
    assert fresh[0]["weight"] == 1.0


# ------------------------------------------------------------------ formations


def test_formation_labels() -> None:
    assert imp.formation_label("srp", ["BB", "BTN"]) == "BTN vs BB - SRP"
    assert imp.formation_label("3bp", ["SB", "CO"]) == "CO vs SB - 3BP"
    assert imp.formation_label("4bp+", ["HJ", "UTG"]) == "UTG vs HJ - 4BP"
    assert imp.formation_label("limped", ["BB", "SB"]) == "SB vs BB - Limped"
    assert imp.spot_formation("pf|BB|vs_open|BTN") == "BTN vs BB - SRP"
    assert imp.spot_formation("pf|BTN|vs_3bet|SB") == "BTN vs SB - 3BP"
    assert imp.spot_formation("pf|BTN|cold|SB") == "BTN vs SB - 3BP"
    assert imp.spot_formation("pf|SB|vs_4bet+|BTN") == "BTN vs SB - 4BP"
    assert imp.spot_formation("pf|CO|rfi|-") is None  # no opponent yet
    assert imp.spot_formation("pf|BB|vs_limp|-") is None
    line = Line("BB", (Step("LJ", "fold"), Step("HJ", "fold"), Step("CO", "fold"), Step("BTN", "raise"),
                       Step("SB", "fold"), Step("BB", "call")), ("gtow", ()))  # fmt: skip
    assert imp.line_formation(line) == "BTN vs BB - SRP"
    assert imp.line_formation(Line("CO", (Step("LJ", "fold"),), ("gtow", ()))) is None


def test_state_formation_and_line_pot() -> None:
    from pn.solve.request import TABLE_SEATS
    from pn.train.engine import apply, next_street, start

    st = start(TABLE_SEATS[6], 10000, 50, 100)
    for kind, to in (("fold", None), ("fold", None), ("fold", None), ("raise", 250), ("fold", None),
                     ("call", None)):  # fmt: skip
        st = apply(st, kind, to)
    assert imp.state_formation(st) is None  # still preflop
    flop = next_street(st, ("Kd", "7s", "2c"))
    assert imp.state_formation(flop) == "BTN vs BB - SRP"
    line = Line("BB", (Step("LJ", "fold"), Step("HJ", "fold"), Step("CO", "fold"), Step("BTN", "raise"),
                       Step("SB", "fold"), Step("BB", "call")), ("gtow", ()))  # fmt: skip
    pot = imp.line_pot_bb(line, 6, 100, {})
    assert pot is not None and 4.0 <= pot <= 7.5  # open + call + the folded SB (trainer sizes)
    rfi = Line("CO", (Step("LJ", "fold"), Step("HJ", "fold")), ("gtow", ()))
    assert imp.line_pot_bb(rfi, 6, 100, {}) == 1.5  # the blinds
    assert (
        imp.line_pot_bb(Line("CO", (Step("BB", "fold"),), ("gtow", ())), 6, 100, {}) is None
    )  # not on turn


# ------------------------------------------------------------------ tables


def test_spot_rows_pot_modes_and_avg_fallback() -> None:
    freq = {"pf|BB|vs_open|BTN": 0.02, "post|srp|ip|flop|first": 0.05}
    meta = {
        "pf|BB|vs_open|BTN": (4.0, "BTN vs BB - SRP"),
        "post|srp|ip|flop|first": (5.5, "BTN vs BB - SRP"),
    }
    rows = [_row("post|srp|ip|flop|first", "blunder", pot_bb=9.0), _row("post|srp|ip|flop|first", "best", pot_bb=11.0),
            _row("pf|BB|vs_open|BTN", "best", pot_bb=None),
            _row("post|srp|ip|flop|first", None, pot_bb=80.0)]  # ungraded: its pot does not count  # fmt: skip
    start = {r["spot"]: r for r in imp.spot_rows(freq, rows, meta, p=3, pot_mode="start")}
    avg = {r["spot"]: r for r in imp.spot_rows(freq, rows, meta, p=3, pot_mode="avg")}
    assert start["post|srp|ip|flop|first"]["pot_bb"] == 5.5
    assert avg["post|srp|ip|flop|first"]["pot_bb"] == 10.0  # the mean pot at hero's decisions
    assert avg["pf|BB|vs_open|BTN"]["pot_bb"] == 4.0  # no recorded pot: falls back to the start pot
    g = imp.global_score(rows)
    s = start["post|srp|ip|flop|first"]
    assert s["score"] == pytest.approx(imp.shrunk(1.0, 2, g), abs=1e-4) and s["n"] == 2
    assert s["formation"] == "BTN vs BB - SRP"
    assert max(r["priority"] for r in start.values()) == 1.0
    assert sum(r["weight"] for r in start.values()) == pytest.approx(1.0, abs=1e-3)
    assert list(start) == sorted(start, key=lambda k: -start[k]["priority"])
    want = {"spot", "label", "freq", "n", "blunders", "blunder_rate", "expected_blunders_per_100", "weight",
            "priority", "score", "pot_bb", "formation"}  # fmt: skip
    assert set(s) == want


def test_texture_table() -> None:
    rows = [_row("post|srp|ip|flop|first", "blunder", board="Kd7s2c", pot_bb=5.5),
            _row("post|srp|ip|turn|first", "blunder", board="Kd7s2c9h", pot_bb=12.0),
            _row("post|srp|oop|flop|first", "best", board="Ah8c3d", pot_bb=5.5),
            _row("post|srp|oop|flop|first", "best", board=None)]  # an old row: no board  # fmt: skip
    out = imp.texture_rows(rows, imp.global_score(rows), start_pot=5.5, p=1, pot_mode="start")
    by = {t["texture"]: t for t in out}
    assert [t["texture"] for t in out] == sorted(by, key=lambda k: -by[k]["priority"])
    assert set(by) == set(tx.TEXTURES)
    assert by["King"]["n"] == 2 and by["Ace"]["n"] == 1 and by["Paired"]["n"] == 0
    assert by["Ace"]["freq"] == pytest.approx(tx.FLOP_SHARE["Ace"], abs=1e-5)
    assert by["King"]["details"] == tx.DETAILS["King"]
    assert all(t["pot_bb"] == 5.5 for t in out)
    assert out[0]["priority"] == 1.0 and out[0]["texture"] == "King"  # two blunders
    avg = {t["texture"]: t for t in imp.texture_rows(rows, 0.7, start_pot=5.5, p=1, pot_mode="avg")}
    # unplayed textures fall back to the mean recorded pot over all textures (one scale), not 5.5
    assert avg["King"]["pot_bb"] == pytest.approx(8.75)
    assert avg["Monotone"]["pot_bb"] == pytest.approx(7.67)
    rows.append(_row("post|srp|ip|flop|first", None, board="Ah8c3d", pot_bb=90.0))  # ungraded
    avg = {t["texture"]: t for t in imp.texture_rows(rows, 0.7, start_pot=5.5, p=1, pot_mode="avg")}
    assert avg["Ace"]["pot_bb"] == 5.5 and avg["Monotone"]["pot_bb"] == pytest.approx(7.67)
    assert sum(t["weight"] for t in out) == pytest.approx(1.0, abs=1e-3)


def test_leaks_importance_over_the_fake_library(tmp_path: Path) -> None:
    import fake_preflop

    from pn.ingest import db as ingest_db
    from pn.solve.lazydoc import LazyDoc
    from pn.train import stats

    lib = LazyDoc.open(fake_preflop.write(tmp_path / "lib.json"))
    model = Leaks(None, library=lambda t, d: lib, freqs={})
    with ingest_db.opened(tmp_path / "pn.sqlite") as conn:
        stats.record_hand(conn, {"hand_uid": "h1", "session_id": "s", "played_at": "2026-10-15T00:00:00",
                                 "table_size": 6, "depth": 100, "source": "gto", "hero_pos": "BB",
                                 "net_bb": 0.0, "opponents_json": "[]"},
                          [{"hand_uid": "h1", "seq": 0, "street": "flop", "position": "BB", "situation": "first",
                            "hand_class": "AKo", "options_json": json.dumps([]), "chosen": "check",
                            "grade": "blunder", "pure": 0, "spot": "post|srp|oop|flop|first",
                            "board": "Kd7s2c", "pot_bb": 5.5, "formation": "BTN vs BB - SRP"}])  # fmt: skip
        out = model.importance(conn, 6, 100, 100, p_form=3, p_tex=1, pot="start")
        with pytest.raises(ValueError):
            model.importance(conn, 6, 100, 100, p_form=9)
        spots_only = model.rows(conn, 6, 100, 100, p_form=1, pot="avg")
    assert set(out) == {"spots", "formations", "textures", "params"}
    assert out["params"] == {"p_form": 3, "p_tex": 1, "pot": "start"}
    rows, forms, texs = out["spots"], out["formations"], out["textures"]
    assert rows[0]["priority"] == 1.0 and all(0 <= r["priority"] <= 1 for r in rows)
    assert [r["priority"] for r in rows] == sorted((r["priority"] for r in rows), reverse=True)
    assert forms and forms[0]["priority"] == 1.0
    f = next(f for f in forms if f["formation"] == "BTN vs BB - SRP")
    assert f["pot_type"] == "SRP" and f["positions"] == ["BTN", "BB"] and f["n"] == 1
    assert f["score"] < imp.global_score([]) and f["pot_bb"] > 1.5 and f["freq"] > 0
    assert set(f["spots"]) <= {r["spot"] for r in rows}
    assert all(r["formation"] == f["formation"] for r in rows if r["spot"] in f["spots"])
    assert sum(x["freq"] for x in forms) == pytest.approx(
        sum(v for k, v in model._map(6, 100).freq.items() if k.startswith("post|")), rel=1e-3)  # fmt: skip
    pre = {r["spot"]: r for r in rows if r["spot"].startswith("pf|")}
    assert all(r["pot_bb"] >= 1.5 for r in pre.values())
    assert next(r for k, r in pre.items() if "|rfi|" in k)["formation"] is None
    assert len(texs) == 8 and next(t for t in texs if t["texture"] == "King")["n"] == 1
    assert {r["spot"] for r in spots_only} == {r["spot"] for r in rows}


def test_returned_fields_reproduce_the_priority() -> None:
    """A client recomputing freq x pot x (1 - score)^p / max from the returned (rounded) fields
    gets the returned priority: the rounding happens before the priority, not after."""
    items = [{"formation": "a", "freq": 0.0000437, "pot_bb": 40.0, "score": 0.5, "n": 0},
             {"formation": "b", "freq": 0.0000249, "pot_bb": 40.0, "score": 0.5, "n": 0},
             {"formation": "c", "freq": 0.0000152, "pot_bb": 40.0, "score": 0.5, "n": 0}]  # fmt: skip
    out = imp._ranked(items, 3, "formation")
    raw = {r["formation"]: r["freq"] * r["pot_bb"] * (1 - r["score"]) ** 3 for r in out}
    top = max(raw.values())
    for r in out:
        assert r["priority"] == pytest.approx(raw[r["formation"]] / top, abs=1e-5)
