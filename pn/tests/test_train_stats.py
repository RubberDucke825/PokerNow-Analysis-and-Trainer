"""RNG buckets (pn.train.rng), trainer situation kinds, rolling stats over synthetic rows
(pn.train.stats) and the `pn train stats` CLI."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn.cli.main import app
from pn.ingest import db as ingest_db
from pn.train import rng as dice
from pn.train import stats
from pn.train.engine import apply, next_street, start


def _opt(token: str, freq: float | None, amount: int | None = None) -> dict:
    return {"token": token, "amount": amount, "freq": freq, "ev_bb": None}


# ------------------------------------------------------------------ buckets


def test_buckets_order_by_aggression_and_size() -> None:
    opts = [_opt("allin", 0.1, 9000), _opt("bet75", 0.2, 300), _opt("check", 0.3),
            _opt("bet33", 0.4, 130)]  # fmt: skip
    assert dice.buckets(opts) == [
        {"token": "check", "from": 1, "to": 30}, {"token": "bet33", "from": 31, "to": 70},
        {"token": "bet75", "from": 71, "to": 90}, {"token": "allin", "from": 91, "to": 100},
    ]  # fmt: skip
    pre = [_opt("raise", 0.5, 250), _opt("call", 0.25, 100), _opt("fold", 0.25, 0)]
    assert [b["token"] for b in dice.buckets(pre)] == ["fold", "call", "raise"]


def test_buckets_round_to_100_and_omit_zero() -> None:
    thirds = dice.buckets([_opt("fold", 1 / 3), _opt("call", 1 / 3), _opt("raise", 1 / 3, 250)])
    widths = [b["to"] - b["from"] + 1 for b in thirds]
    assert sum(widths) == 100 and sorted(widths) == [33, 33, 34] and thirds[-1]["to"] == 100
    got = dice.buckets([_opt("fold", 0.004), _opt("call", 0.4), _opt("raise", 0.596, 250)])
    assert [b["token"] for b in got] == ["call", "raise"]  # 0.4% rounds to 0
    assert got == [{"token": "call", "from": 1, "to": 40}, {"token": "raise", "from": 41, "to": 100}]
    unnormalised = dice.buckets([_opt("check", 0.5), _opt("bet50", 0.5001, 100)])  # rounded freqs
    assert unnormalised[-1]["to"] == 100
    assert dice.buckets([_opt("check", None), _opt("bet33", None, 50)]) == []
    assert dice.buckets([_opt("check", 0.0)]) == []


def test_judge_mixed_pure_and_disabled() -> None:
    mixed = [_opt("check", 0.3), _opt("bet50", 0.7, 100)]
    j = dice.judge(mixed, "check", 30)
    assert j["rng_action"] == "check" and j["matched_rng"] is True and not j["pure"]
    assert dice.judge(mixed, "check", 31)["rng_action"] == "bet50"
    assert dice.judge(mixed, "check", 31)["matched_rng"] is False
    pure = [_opt("fold", 0.006), _opt("call", 0.994)]
    j = dice.judge(pure, "call", 1)  # roll lands in fold's 1-slot bucket; pure: call matches
    assert j["pure"] and j["rng_action"] == "call" and j["matched_rng"] is True
    off = dice.judge(mixed, "check", None)
    assert off["rng"] is None and off["matched_rng"] is None and off["buckets"]
    assert dice.judge([_opt("check", None)], "check", 50)["matched_rng"] is None


# ------------------------------------------------------------------ situation kinds


def test_situation_kinds() -> None:
    order = ("LJ", "HJ", "CO", "BTN", "SB", "BB")
    st = start(order, 10_000, 50, 100)
    assert stats.situation_kind(st, 0) == "rfi"
    st = apply(st, "raise", 250)  # LJ opens
    assert stats.situation_kind(st, 1) == "vs_open"
    st = apply(st, "raise", 750)  # HJ 3-bets
    assert stats.situation_kind(st, 2) == "cold"
    for _ in range(3):
        st = apply(st, "fold")  # CO BTN SB
    st = apply(st, "fold")  # BB
    assert stats.situation_kind(st, 0) == "vs_3bet"
    st4 = apply(st, "raise", 1700)
    assert stats.situation_kind(st4, 1) == "vs_4bet+"
    st = apply(st, "call")
    flop = next_street(st, ("Kd", "7s", "2c"))
    assert stats.situation_kind(flop, flop.to_act) == "first"
    bet = apply(flop, "raise", 500)
    assert stats.situation_kind(bet, bet.to_act) == "facing_bet"
    raised = apply(bet, "raise", 1500)
    assert stats.situation_kind(raised, raised.to_act) == "facing_raise"


# ------------------------------------------------------------------ rolling


def _seed(conn: sqlite3.Connection, n: int, source: str = "gto", start_at: int = 0,
          flop: str = "blunder", matched: int = 1, pure: int = 0) -> None:  # fmt: skip
    """n hands, each with a preflop decision graded best and a flop decision graded `flop`."""
    for k in range(start_at, start_at + n):
        uid = f"h{k:05d}"
        stats.record_hand(conn, {
            "hand_uid": uid, "session_id": "s", "played_at": f"2026-10-02T00:00:00.{k:06d}+00:00",
            "table_size": 6, "depth": 100, "source": source, "hero_pos": "BTN" if k % 2 else "BB",
            "net_bb": 1.0, "opponents_json": "[]"}, [
            {"hand_uid": uid, "seq": 0, "street": "preflop", "position": "BTN",
             "situation": "rfi", "hand_class": "AKs", "rng": 10, "options_json": "[]",
             "chosen": "raise", "rng_action": "raise", "best": "raise", "loss_bb": None,
             "grade": "best", "matched_rng": matched, "pure": pure},
            {"hand_uid": uid, "seq": 1, "street": "flop", "position": "BTN",
             "situation": "first", "hand_class": "AKs", "rng": None, "options_json": "[]",
             "chosen": "check", "rng_action": None, "best": "bet33", "loss_bb": None,
             "grade": flop, "matched_rng": None, "pure": 0},
        ])  # fmt: skip


@pytest.fixture
def conn(tmp_path: Path):
    c = ingest_db.connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def test_rolling_empty(conn: sqlite3.Connection) -> None:
    s = stats.rolling(conn)
    assert s["hands"] == 0 and s["graded"] == 0 and s["blunder_rate"] is None and s["slices"] == []
    assert s["rng_match_rate"] is None and s["by_street"] == {}
    assert not {"loss_bb", "bb_per_100"} & set(s)
    with pytest.raises(ValueError):
        stats.rolling(conn, 0)


def test_rolling_window_boundary_and_slices(conn: sqlite3.Connection) -> None:
    _seed(conn, 1, flop="best", matched=0)  # the oldest hand: outside a 1000-hand window
    _seed(conn, 1000, start_at=1)
    s = stats.rolling(conn, 1000)
    assert s["hands"] == 1000 and s["decisions"] == 2000 and s["graded"] == 2000
    assert (s["best"], s["mixed"], s["blunders"]) == (1000, 0, 1000)
    assert s["blunder_rate"] == 0.5 and s["best_rate"] == 0.5 and s["mixed_rate"] == 0.0
    assert s["rng_decisions"] == 1000 and s["rng_match_rate"] == 1.0
    assert len(s["slices"]) == 10 and s["slices"][0] == {
        "from_hand": 1, "to_hand": 100, "hands": 100, "decisions": 200, "graded": 200,
        "blunder_rate": 0.5, "rng_match_rate": 1.0}  # fmt: skip
    pre, flop = s["by_street"]["preflop"], s["by_street"]["flop"]
    assert list(s["by_street"]) == ["preflop", "flop"]
    assert pre["best_rate"] == 1.0 and pre["blunder_rate"] == 0.0 and pre["rng_match_rate"] == 1.0
    assert flop["decisions"] == 1000 and flop["blunder_rate"] == 1.0 and flop["rng_match_rate"] is None
    assert s["by_position"]["BB"]["hands"] == 500 and s["by_situation"]["first"]["blunders"] == 1000
    wide = stats.rolling(conn, 1001)
    assert wide["hands"] == 1001 and wide["rng_match_rate"] == pytest.approx(1000 / 1001, abs=1e-4)
    assert wide["slices"][0]["blunder_rate"] == pytest.approx(99 / 200)
    assert stats.totals(s) == {"window": 1000, "hands": 1000, "rng_match_rate": 1.0,
                               "blunder_rate": 0.5, "mixed_rate": 0.0, "best_rate": 0.5}  # fmt: skip


def test_rolling_partial_slice_source_filter_and_pure(conn: sqlite3.Connection) -> None:
    _seed(conn, 250, source="gto", flop="mixed")
    _seed(conn, 30, source="legacy", start_at=250, pure=1, matched=0)  # a pre-Plan-7 stored source
    gto = stats.rolling(conn, source="gto")
    assert gto["hands"] == 250 and [sl["hands"] for sl in gto["slices"]] == [100, 100, 50]
    assert gto["blunders"] == 0 and gto["mixed"] == 250 and gto["mixed_rate"] == 0.5
    old = stats.rolling(conn, source="legacy")
    assert old["hands"] == 30 and old["rng_decisions"] == 0 and old["rng_match_rate"] is None
    every = stats.rolling(conn)
    assert every["hands"] == 280 and every["natural_hands"] == 280 and every["drill_hands"] == 0
    conn.execute("UPDATE train_hands SET drill_spot = 'pf|CO|rfi|-', drill_landed = 1 "
                 "WHERE hand_uid IN ('h00000', 'h00001')")  # fmt: skip
    conn.execute("UPDATE train_hands SET drill_spot = 'pf|CO|rfi|-', drill_landed = 0 "
                 "WHERE hand_uid = 'h00002'")  # fmt: skip
    again = stats.rolling(conn)
    assert (again["drill_hands"], again["natural_hands"], again["drills_landed"]) == (3, 277, 2)


def test_legacy_ev_and_freq_rows_are_regraded_from_their_options(conn: sqlite3.Connection) -> None:
    """Rows written before Plan 7 (grade "ev" / "freq") count by their stored frequencies;
    ungraded rows ("none") and rows without frequencies do not count."""
    opts = '[{"token": "check", "freq": 0.05}, {"token": "bet33", "freq": 0.95}]'
    rows = [("ev", opts, "check"), ("freq", opts, "bet33"), ("none", opts, "check"),
            ("ev", '[{"token": "check", "freq": null}]', "check")]  # fmt: skip
    stats.record_hand(conn, {"hand_uid": "old", "session_id": "s", "played_at": "2026-10-01",
                             "table_size": 6, "depth": 100, "source": "gto",
                             "hero_pos": "BB", "net_bb": 0.0, "opponents_json": "[]"},
                      [{"hand_uid": "old", "seq": k, "street": "flop", "position": "BB",
                        "situation": "first", "hand_class": "AKs", "options_json": o,
                        "chosen": c, "best": "bet33", "grade": g, "pure": 0}
                       for k, (g, o, c) in enumerate(rows)])  # fmt: skip
    s = stats.rolling(conn)
    assert (s["decisions"], s["graded"], s["best"], s["blunders"]) == (4, 2, 1, 1)


# ------------------------------------------------------------------ CLI


def test_cli_train_stats(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    with ingest_db.opened(db) as c:
        _seed(c, 120)
    r = CliRunner().invoke(app, ["train", "stats", "--db", str(db), "--window", "100"])
    assert r.exit_code == 0, r.output
    assert "100 hands (window 100)" in r.output and "100 (50%)" in r.output
    assert "street: flop" in r.output and "hands 1-100" in r.output
    missing = CliRunner().invoke(app, ["train", "stats", "--db", str(tmp_path / "none.sqlite")])
    assert missing.exit_code == 1


# ------------------------------------------------------------------ Plan 14 columns


def test_plan14_columns_are_added_to_an_old_database_and_old_rows_stay_null(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    with ingest_db.opened(path) as c:  # a database from before Plan 14: drop the new columns
        for col in ("board", "pot_bb", "formation"):
            c.execute(f"ALTER TABLE train_decisions DROP COLUMN {col}")
        c.execute("INSERT INTO train_decisions (hand_uid, seq, street, position, situation, hand_class, "
                  "options_json, chosen, grade) VALUES ('old', 0, 'flop', 'BB', 'first', 'AKo', '[]', "
                  "'check', 'best')")  # fmt: skip
        c.commit()
    with ingest_db.opened(path) as c:
        stats.ensure(c)
        cols = {r[1]: r[2] for r in c.execute("PRAGMA table_info(train_decisions)")}
        assert cols["board"] == "TEXT" and cols["pot_bb"] == "REAL" and cols["formation"] == "TEXT"
        assert [tuple(r) for r in c.execute("SELECT board, pot_bb, formation FROM train_decisions")] == [(None,) * 3]
        stats.ensure(c)  # idempotent


def test_ensure_tolerates_a_concurrent_migration(tmp_path: Path) -> None:
    """Two readers of an old database both see the columns missing; the one that ALTERs second
    must not fail (the web UI fetches /leaks and /importance in parallel)."""
    path = tmp_path / "old.sqlite"
    with ingest_db.opened(path) as c:
        for col in ("board", "pot_bb", "formation"):
            c.execute(f"ALTER TABLE train_decisions DROP COLUMN {col}")
        c.commit()

    class Racing:  # the other reader migrates between this one's PRAGMA and its first ALTER
        def __init__(self, conn: sqlite3.Connection) -> None:
            self.conn, self.raced = conn, False

        def execute(self, sql: str, *args):
            if sql.startswith("ALTER") and not self.raced:
                self.raced = True
                with ingest_db.opened(path) as other:
                    stats.ensure(other)
            return self.conn.execute(sql, *args)

        def commit(self) -> None:
            self.conn.commit()

    with ingest_db.opened(path) as c:
        stats.ensure(Racing(c))
        cols = {r[1] for r in c.execute("PRAGMA table_info(train_decisions)")}
        assert {"board", "pot_bb", "formation"} <= cols


def test_decision_row_stores_the_board_pot_and_formation_postflop_only() -> None:
    fb = {"street": "flop", "position": "BB", "rng": None, "options": [], "chosen": "check",
          "rng_action": None, "best": "check", "loss_bb": None, "grade": "best", "matched_rng": None,
          "pure": False, "board": ["Kd", "7s", "2c"], "pot": 550}  # fmt: skip
    row = stats.decision_row("h-1", 0, fb, "first", "AKo", "post|srp|oop|flop|first", formation="BTN vs BB - SRP")
    assert row["board"] == "Kd7s2c" and row["pot_bb"] == 5.5 and row["formation"] == "BTN vs BB - SRP"
    pf = stats.decision_row("h-1", 1, {**fb, "street": "preflop", "board": [], "pot": 150}, "rfi", "AKo")
    assert pf["board"] is None and pf["formation"] is None and pf["pot_bb"] == 1.5
