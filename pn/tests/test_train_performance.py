"""GET /api/train/performance backing (pn.train.performance; contracts/trainer.md "Plan 15 additions")."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest

from pn.ingest import db as ingest_db
from pn.train import performance as perf
from pn.train import stats

_N = 0


def _opts(**freqs: float) -> str:
    return json.dumps(
        [{"token": t.rstrip("_"), "amount": 0, "freq": f, "ev_bb": None} for t, f in freqs.items()]
    )


def _hand(conn: sqlite3.Connection, decisions: list[dict], *, table: int = 6, depth: int = 100,
          at: str = "2026-10-10T12:00:00+00:00", hero: str = "BTN") -> str:  # fmt: skip
    global _N
    _N += 1
    uid = f"h{_N:05d}"
    rows = [{"hand_uid": uid, "seq": k, "street": "preflop", "position": hero, "situation": "rfi",
             "hand_class": "AKs", "rng": None, "options_json": _opts(raise_=1.0), "chosen": "raise",
             "rng_action": None, "best": "raise", "loss_bb": None, "grade": "best", "matched_rng": None,
             "pure": 1, "spot": f"pf|{hero}|rfi|-", "note": None, **d}
            for k, d in enumerate(decisions)]  # fmt: skip
    stats.record_hand(conn, {"hand_uid": uid, "session_id": "s", "played_at": at, "table_size": table,
                             "depth": depth, "source": "gto", "hero_pos": hero, "net_bb": 0.0,
                             "opponents_json": "[]"}, rows)  # fmt: skip
    return uid


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = ingest_db.connect(":memory:")
    yield c
    c.close()


def test_empty_db(conn: sqlite3.Connection) -> None:
    out = perf.performance(conn)
    assert out["rows"] == [] and out["trend"] == []
    assert out["totals"] == {"decisions": 0, "graded": 0, "legacy": 0, "ungraded": 0, "best": 0,
                             "mixed": 0, "blunders": 0, "closeness": None, "rng_match": None,
                             "rng_n": 0}  # fmt: skip
    assert out["axes"]["positions"] == ["LJ", "HJ", "CO", "BTN", "SB", "BB"]
    assert out["axes"]["families"] == []


def test_closeness_from_options() -> None:
    assert perf.closeness(_opts(fold=0.2, call=0.5, raise_=0.3), "call") == 1.0
    assert perf.closeness(_opts(fold=0.2, call=0.5, raise_=0.3), "raise") == 0.6
    assert perf.closeness(_opts(fold=1.0, call=0.0), "call") == 0.0
    assert perf.closeness(_opts(fold=1.0), "allin") == 0.0  # not an option: never taken
    assert perf.closeness(_opts(fold=0.0, call=0.0), "call") is None
    assert perf.closeness("[]", "call") is None
    assert perf.closeness("not json", "call") is None
    assert perf.closeness(json.dumps([{"token": "fold", "freq": None}]), "fold") is None


def test_grades_legacy_and_ungraded(conn: sqlite3.Connection) -> None:
    spot = "pf|BTN|vs_open|CO"
    _hand(conn, [
        {"spot": spot, "options_json": _opts(fold=0.5, call=0.25, raise_=0.25), "chosen": "fold",
         "grade": "best", "matched_rng": 1, "pure": 0},
        {"spot": spot, "options_json": _opts(fold=0.5, call=0.25, raise_=0.25), "chosen": "call",
         "grade": "mixed", "matched_rng": 0, "pure": 0},
        {"spot": spot, "options_json": _opts(fold=1.0, call=0.0), "chosen": "call", "grade": "blunder",
         "best": "fold"},
        {"spot": spot, "options_json": _opts(fold=1.0, call=0.0), "chosen": "fold", "grade": "freq"},
        {"spot": spot, "options_json": _opts(fold=1.0, call=0.0), "chosen": "call", "grade": "ev"},
        {"spot": spot, "options_json": "[]", "chosen": "call", "grade": "none",
         "note": "combo_outside_range"},
    ])  # fmt: skip
    out = perf.performance(conn)
    t = out["totals"]
    assert (t["decisions"], t["graded"], t["legacy"], t["ungraded"]) == (6, 3, 2, 1)
    assert (t["best"], t["mixed"], t["blunders"]) == (1, 1, 1)
    assert t["closeness"] == 0.5  # (1 + 0.5 + 0) / 3; legacy and ungraded rows excluded
    assert t["rng_match"] == 0.5
    [row] = out["rows"]
    assert row == {"spot": spot, "label": "BTN vs CO open", "street": "preflop", "position": "BTN",
                   "family": "vs_open", "pot_type": None, "n": 6, "graded": 3, "legacy": 2,
                   "best": 1, "mixed": 1, "blunders": 1, "closeness": 0.5, "rng_match": 0.5,
                   "rng_n": 2, "last_played": "2026-10-10T12:00:00+00:00"}  # fmt: skip
    assert t["rng_n"] == 2
    recent = perf.cell_recent(conn, "BTN", "vs_open")["recent"]
    assert len(recent) == 3  # graded decisions only, newest first
    assert recent[0] == {"played_at": "2026-10-10T12:00:00+00:00", "hand_class": "AKs",
                         "chosen": "call", "chosen_freq": 0.0, "best": "fold", "best_freq": 1.0,
                         "grade": "blunder", "closeness": 0.0, "spot": spot,
                         "label": "BTN vs CO open"}  # fmt: skip


def test_family_and_pot_type_per_spot_kind(conn: sqlite3.Connection) -> None:
    _hand(conn, [{"spot": "pf|BTN|rfi|-"}, {"spot": "pf|BTN|vs_limp|-"}, {"spot": "pf|BTN|vs_3bet|SB"},
                 {"spot": "pf|BTN|vs_4bet+|SB"}, {"spot": "pf|BTN|cold|HJ"},
                 {"spot": "post|srp|ip|flop|first", "street": "flop"},
                 {"spot": "post|srp|oop|flop|facing_bet", "street": "flop"},
                 {"spot": "post|3bp|ip|turn|facing_raise", "street": "turn"},
                 {"spot": "post|limped|oop|river|first", "street": "river"},
                 {"spot": "post|4bp+|ip|flop|first", "street": "flop"},
                 {"spot": None, "street": "turn"}, {"spot": "garbage", "street": "flop"}])  # fmt: skip
    rows = {(r["spot"], r["position"]): r for r in perf.performance(conn)["rows"]}
    fam = {k[0]: (r["family"], r["pot_type"], r["street"]) for k, r in rows.items()}
    assert fam["pf|BTN|rfi|-"] == ("rfi", None, "preflop")
    assert fam["pf|BTN|vs_limp|-"] == ("vs_limp", None, "preflop")
    assert fam["pf|BTN|vs_4bet+|SB"] == ("vs_4bet+", None, "preflop")
    assert fam["pf|BTN|cold|HJ"] == ("cold", None, "preflop")
    assert fam["post|srp|ip|flop|first"] == ("srp|flop|first", "srp", "flop")
    assert fam["post|srp|oop|flop|facing_bet"] == ("srp|flop|facing_bet", "srp", "flop")
    assert fam["post|3bp|ip|turn|facing_raise"] == ("3bp|turn|facing_raise", "3bp", "turn")
    assert fam["post|limped|oop|river|first"] == ("limped|river|first", "limped", "river")
    assert fam["post|4bp+|ip|flop|first"] == ("4bp+|flop|first", "4bp+", "flop")
    assert fam[None] == ("other", None, "turn") and fam["garbage"] == ("other", None, "flop")
    assert rows[(None, "BTN")]["label"] == "Other"


def test_axes_order(conn: sqlite3.Connection) -> None:
    _hand(conn, [{"spot": "post|3bp|ip|flop|first", "street": "flop"},
                 {"spot": "post|srp|ip|river|facing_bet", "street": "river"},
                 {"spot": "post|srp|ip|flop|facing_bet", "street": "flop"},
                 {"spot": "post|srp|ip|flop|first", "street": "flop"},
                 {"spot": "pf|BTN|cold|HJ"}, {"spot": "pf|BTN|rfi|-"}, {"spot": None, "street": "turn"}])  # fmt: skip
    _hand(conn, [{"spot": "pf|UTG1|rfi|-"}], table=8, hero="UTG1")
    fams = perf.performance(conn, table=6)["axes"]["families"]
    assert [f["id"] for f in fams] == ["rfi", "cold", "srp|flop|first", "srp|flop|facing_bet",
                                       "3bp|flop|first", "srp|river|facing_bet", "other"]  # fmt: skip
    assert fams[0] == {"id": "rfi", "label": "RFI", "street": "preflop", "pot_type": None}
    assert fams[3] == {"id": "srp|flop|facing_bet", "label": "SRP flop vs bet", "street": "flop",
                       "pot_type": "srp"}  # fmt: skip
    assert fams[-1]["street"] is None
    assert perf.performance(conn, table=6)["axes"]["positions"] == [
        "LJ",
        "HJ",
        "CO",
        "BTN",
        "SB",
        "BB",
    ]
    assert perf.performance(conn, table=8)["axes"]["positions"] == [
        "UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]  # fmt: skip
    assert perf.performance(conn)["axes"]["positions"][:2] == ["UTG", "UTG1"]  # both sizes present


def test_filters(conn: sqlite3.Connection) -> None:
    _hand(conn, [{"grade": "blunder", "options_json": _opts(raise_=1.0, fold=0.0), "chosen": "fold"}],
          at="2026-10-01T10:00:00+00:00")  # fmt: skip
    _hand(conn, [{}], at="2026-10-05T10:00:00+00:00", depth=200)
    _hand(conn, [{}], at="2026-10-06T10:00:00+00:00", table=8)
    _hand(conn, [{}], at="2026-10-07T10:00:00+00:00")
    assert perf.performance(conn)["totals"]["decisions"] == 4
    assert perf.performance(conn, table=6)["totals"]["decisions"] == 3
    assert perf.performance(conn, table=8)["totals"]["decisions"] == 1
    assert perf.performance(conn, depth=200)["totals"]["decisions"] == 1
    assert perf.performance(conn, since="2026-10-05")["totals"]["decisions"] == 3
    assert perf.performance(conn, since="2026-10-06T10:00:00+00:00")["totals"]["decisions"] == 2
    w = perf.performance(conn, window=3)["totals"]
    assert w["decisions"] == 3 and w["blunders"] == 0  # the oldest hand dropped
    assert perf.performance(conn, table=6, depth=100, window=1)["totals"]["decisions"] == 1
    assert perf.performance(conn, table=6, window=2)["totals"]["blunders"] == 0
    for bad in ({"window": 0}, {"table": 9}, {"depth": 0}, {"since": "yesterday"}):
        with pytest.raises(ValueError):
            perf.performance(conn, **bad)


def test_trend_buckets(conn: sqlite3.Connection) -> None:
    for k in range(12):
        g = "blunder" if k < 4 else "best"
        _hand(conn, [{"grade": g, "chosen": "fold" if k < 4 else "raise",
                      "options_json": _opts(raise_=1.0, fold=0.0)}],
              at=f"2026-10-{k + 1:02d}T10:00:00+00:00")  # fmt: skip
    _hand(conn, [{"grade": "freq"}], at="2026-10-20T10:00:00+00:00")  # legacy: not in the trend
    trend = perf.performance(conn)["trend"]
    assert [b["n"] for b in trend] == [5, 5, 2]  # TREND_MIN decisions per bucket, oldest first
    assert trend[0] == {"bucket": "1-5", "from": 1, "to": 5, "date": "2026-10-05", "n": 5,
                        "closeness": 0.2, "blunder_rate": 0.8}  # fmt: skip
    assert trend[2]["closeness"] == 1.0 and trend[2]["blunder_rate"] == 0.0


def test_trend_bucket_size_grows(conn: sqlite3.Connection) -> None:
    _hand(conn, [{} for _ in range(perf.TREND_BUCKETS * 10)])
    trend = perf.performance(conn)["trend"]
    assert len(trend) == perf.TREND_BUCKETS and all(b["n"] == 10 for b in trend)


def test_recent_capped_and_rows_sorted(conn: sqlite3.Connection) -> None:
    for k in range(25):
        _hand(conn, [{}], at=f"2026-10-10T12:00:{k:02d}+00:00")
    _hand(conn, [{"spot": "pf|BB|vs_open|BTN"}], hero="BB")
    rows = perf.performance(conn)["rows"]
    assert [r["position"] for r in rows] == ["BTN", "BB"]  # table order
    assert "recent" not in rows[0]  # served per cell (cell_recent), keeps the list response small
    recent = perf.cell_recent(conn, "BTN", "rfi")["recent"]
    assert len(recent) == perf.RECENT == 20
    assert recent[0]["played_at"] == "2026-10-10T12:00:24+00:00"
    assert rows[0]["last_played"] == "2026-10-10T12:00:24+00:00"
    last3 = perf.cell_recent(conn, "BTN", "rfi", window=3)["recent"]
    assert [d["played_at"][-11:-6] for d in last3] == ["00:24", "00:23", "00:22"]
    assert perf.cell_recent(conn, "CO", "rfi")["recent"] == []


def test_cell_recent_merges_spots_of_a_family(conn: sqlite3.Connection) -> None:
    _hand(conn, [{"spot": "pf|BB|vs_open|BTN"}], hero="BB", at="2026-10-10T12:00:00+00:00")
    _hand(conn, [{"spot": "pf|BB|vs_open|CO"}], hero="BB", at="2026-10-11T12:00:00+00:00")
    _hand(conn, [{"spot": "pf|BB|vs_3bet|BTN"}], hero="BB", at="2026-10-12T12:00:00+00:00")
    _hand(conn, [{"spot": None, "street": "flop"}, {"spot": "pf|BB|squeeze|-"}], hero="BB")
    out = perf.cell_recent(conn, "BB", "vs_open")
    assert [d["spot"] for d in out["recent"]] == ["pf|BB|vs_open|CO", "pf|BB|vs_open|BTN"]
    assert {d["label"] for d in perf.cell_recent(conn, "BB", "other")["recent"]} == {"Other"}


def test_unknown_spot_kinds_are_other(conn: sqlite3.Connection) -> None:
    bad = ["pf|BTN|squeeze|-", "post|xx|ip|flop|first", "post|srp|ip|preflop|first",
           "post|srp|ip|flop|donk"]  # fmt: skip
    _hand(conn, [{"spot": s} for s in bad] + [{}])
    out = perf.performance(conn)
    fams = {r["spot"]: r["family"] for r in out["rows"]}
    assert all(fams[s] == "other" for s in bad) and fams["pf|BTN|rfi|-"] == "rfi"
    assert [f["id"] for f in out["axes"]["families"]] == ["rfi", "other"]


def test_rng_n_counts_rolls_of_any_kind(conn: sqlite3.Connection) -> None:
    _hand(conn, [{"grade": "freq", "matched_rng": 1, "pure": 0},
                 {"grade": "none", "matched_rng": 0, "pure": 0}])  # fmt: skip
    [row] = perf.performance(conn)["rows"]
    assert (row["graded"], row["rng_n"], row["rng_match"]) == (0, 2, 0.5)


def test_since_naive_is_utc_and_normalised(conn: sqlite3.Connection) -> None:
    _hand(conn, [{}], at="2026-10-15T17:00:00+00:00")
    _hand(conn, [{}], at="2026-10-15T18:00:00+00:00")
    assert perf.performance(conn, since="2026-10-15 17:30:00")["totals"]["decisions"] == 1
    assert perf.performance(conn, since="2026-10-15T17:30:00")["totals"]["decisions"] == 1
    assert perf.performance(conn, since="2026-10-15T19:30:00+02:00")["totals"]["decisions"] == 1
    assert perf.performance(conn, since="2026-10-15")["totals"]["decisions"] == 2


def test_huge_ints_rejected(conn: sqlite3.Connection) -> None:
    for bad in ({"window": 10**20}, {"depth": 10**20}):
        with pytest.raises(ValueError):
            perf.performance(conn, **bad)


def test_route(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from pn.web.app import create_app

    (tmp_path / "solves").mkdir()
    client = TestClient(create_app(tmp_path / "pn.sqlite", tmp_path / "solves"))
    empty = client.get("/api/train/performance", params={"table": "", "depth": "", "since": ""})
    assert empty.status_code == 200 and empty.json()["rows"] == []
    with ingest_db.opened(tmp_path / "pn.sqlite") as c:
        _hand(c, [{}], table=8, hero="UTG")
        _hand(c, [{}])
    out = client.get("/api/train/performance", params={"table": 6, "window": 250}).json()
    assert out["totals"]["decisions"] == 1 and out["axes"]["positions"][0] == "LJ"
    assert client.get("/api/train/performance").json()["totals"]["decisions"] == 2
    assert (
        client.get("/api/train/performance", params={"window": "all"}).json()["totals"]["decisions"]
        == 2
    )
    for bad in ({"table": 7}, {"window": 0}, {"window": "x"}, {"since": "nope"}, {"depth": -1},
                {"window": "99999999999999999999"}, {"depth": "99999999999999999999"}):  # fmt: skip
        assert client.get("/api/train/performance", params=bad).status_code == 400
    cell = client.get("/api/train/performance/recent", params={"position": "BTN", "family": "rfi"})
    assert cell.status_code == 200 and len(cell.json()["recent"]) == 1
    assert client.get("/api/train/performance/recent", params={"family": "rfi"}).status_code == 400
    js = client.get("/static/js/views/performance.js", headers={"accept-encoding": "gzip"})
    assert js.headers.get("content-encoding") == "gzip"  # GZipMiddleware (large payloads)
