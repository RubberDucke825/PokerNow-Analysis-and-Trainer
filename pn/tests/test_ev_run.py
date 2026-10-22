"""pn ev run / show / summary and the two review API routes, on a DB built from the 298-hand
fixture log (villain cards from showdowns), with a fake TexasSolver-shaped runner
(fixtures/fake_tree.py: strategies, no EVs) so nothing real is solved."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn.cli.main import app
from pn.ev.report import player_ids, summary
from pn.ev.run import run
from pn.ingest import db
from pn.solve import SolveCache

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from fake_tree import fake_runner

GAME = "pglSAMPLE0000000000000000"
LOG = Path(__file__).resolve().parents[2] / "data" / "fixtures" / f"pokernow_{GAME}.json"
LIMIT = 6


def _cache(root: Path) -> SolveCache:
    return SolveCache(root, runner=fake_runner)


@pytest.fixture(autouse=True)
def fake_solves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`pn ev run` without a cache argument (the CLI) solves with the fake runner too."""
    import pn.ev.run as ev_run

    monkeypatch.setattr(ev_run, "SolveCache", lambda: _cache(tmp_path / "cache"))


@pytest.fixture(scope="module")
def dbfile(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not LOG.exists():
        pytest.skip("real fixture log missing")
    from pn.ingest.parse import parse_messages, sort_messages

    hands = parse_messages(sort_messages(json.loads(LOG.read_text())), game_id=GAME)
    path = tmp_path_factory.mktemp("ev") / "pn.sqlite"
    db.load(hands, path)
    return path


def _copy(dbfile: Path, tmp_path: Path) -> Path:
    out = tmp_path / "pn.sqlite"
    out.write_bytes(dbfile.read_bytes())
    return out


def _rows(path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM decision_ev ORDER BY hand_id, seq").fetchall()
    finally:
        conn.close()


def test_run_is_idempotent_and_fills_rows(dbfile: Path, tmp_path: Path) -> None:
    path = _copy(dbfile, tmp_path)
    cache = _cache(tmp_path / "c")
    lines: list[str] = []
    first = run(path, source="gto", limit=LIMIT, cache=cache, emit=lines.append)
    rows = _rows(path)
    assert first["graded"] + first["null"] == len(rows) > 0 and first["skipped"] == 0
    assert first["graded"] > 0 and lines
    graded = [r for r in rows if r["grade"] is not None]
    assert graded and {r["grade"] for r in graded} <= {"best", "mixed", "blunder"}
    assert all(0 <= r["freq_chosen"] <= r["best_freq"] <= 1 for r in graded)
    assert {r["street"] for r in graded} >= {"preflop", "flop"}  # preflop by chart, flop solved
    assert all(r["loss"] is None and r["ev_best"] is None for r in rows)  # texas: no EVs
    assert all(r["exploitability"] is None for r in rows)  # column exists; fake meta has none
    assert all(r["note"] for r in rows if r["grade"] is None)
    assert {r["source"] for r in rows} == {"gto"} and all(r["spot"] for r in rows)
    again = run(path, source="gto", limit=LIMIT, cache=cache, emit=lines.append)
    assert again["graded"] == again["null"] == 0 and again["skipped"] == len(rows)
    forced = run(path, source="gto", limit=LIMIT, cache=cache, force=True, emit=lines.append)
    assert forced["graded"] + forced["null"] == len(rows) == len(_rows(path))


def test_run_rejects_bad_source(dbfile: Path) -> None:
    for source in ("nope", "tendencies", "custom", "population"):  # Plan 7: gto only
        with pytest.raises(ValueError):
            run(dbfile, source=source)


def test_summary_structure(dbfile: Path, tmp_path: Path) -> None:
    path = _copy(dbfile, tmp_path)
    run(path, source="gto", limit=LIMIT, cache=_cache(tmp_path / "c"), emit=lambda _m: None)
    pid = _rows(path)[0]["player_id"]
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        s = summary(conn, player_ids(conn, pid), "gto")
        none = summary(conn, player_ids(conn, pid), "legacy")  # a source with no rows
    finally:
        conn.close()
    assert s["decisions"] == s["graded"] + s["null"] > 0
    assert sum(g["n"] for g in s["by_street"]) == s["decisions"]
    assert sum(g["n"] for g in s["by_spot"]) == s["decisions"]
    assert set(s["compare"]) == {"gto"} and none["decisions"] == 0
    assert len(s["top_hands"]) <= 10
    assert s["graded"] == s["best"] + s["mixed"] + s["blunders"]
    assert sum(g["blunders"] for g in s["by_street"]) == s["blunders"]
    assert not {"total_loss_bb", "loss_per_100"} & set(s)


def test_cli_run_show_summary(dbfile: Path, tmp_path: Path) -> None:
    path = _copy(dbfile, tmp_path)
    cli = CliRunner()
    r = cli.invoke(app, ["ev", "run", "--source", "gto", "--limit", "3", "--db", str(path)])
    assert r.exit_code == 0, r.output
    assert "graded" in r.output
    row = _rows(path)[0]
    r = cli.invoke(app, ["ev", "show", row["hand_id"], "--source", "gto", "--db", str(path)])
    assert r.exit_code == 0, r.output
    assert row["hand_id"] in r.output
    r = cli.invoke(app, ["ev", "summary", row["player_id"], "--source", "gto", "--db", str(path)])
    assert r.exit_code == 0 and "by street" in r.output, r.output
    r = cli.invoke(app, ["ev", "show", "nohand", "--db", str(path)])
    assert r.exit_code == 1


def test_api_routes(dbfile: Path, tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from pn.web.app import create_app

    path = _copy(dbfile, tmp_path)
    run(path, source="gto", limit=LIMIT, cache=_cache(tmp_path / "c"), emit=lambda _m: None)
    row = _rows(path)[0]
    client = TestClient(create_app(path, tmp_path / "solves"))
    r = client.get(f"/api/hands/{row['hand_id']}/ev?source=gto").json()
    assert r["source"] == "gto" and r["rows"]
    assert {"loss_bb", "grade", "freq_chosen", "best_freq", "note"} <= set(r["rows"][0])
    assert client.get(f"/api/hands/{row['hand_id']}/ev?source=tendencies").status_code == 400
    assert client.get(f"/api/hands/{row['hand_id']}/ev").json()["source"] == "gto"  # default
    s = client.get(f"/api/review/{row['player_id']}/ev?source=gto").json()
    assert s["decisions"] > 0 and {"by_street", "by_spot", "top_hands", "compare"} <= set(s)
    assert client.get(f"/api/review/{row['player_id']}/ev?source=bad").status_code == 400
    assert client.get("/api/review/nobody-here/ev").status_code == 404



def test_unmodeled_rows_are_kept_out_of_totals(dbfile: Path, tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from pn.web.app import create_app

    path = _copy(dbfile, tmp_path)
    db.connect(path).close()  # creates decision_ev
    conn = sqlite3.connect(path)
    hid, pid = conn.execute("SELECT hand_id, player_id FROM hand_players LIMIT 1").fetchone()
    rows = [(1, "flop", "blunder", None), (2, "turn", "blunder", "combo_added_to_range"),
            (3, "river", "mixed", None)]  # fmt: skip
    conn.executemany(
        "INSERT INTO decision_ev (hand_id, seq, source, street, player_id, chosen, best, "
        "computed_at, note, spot, grade, freq_chosen, best_freq) "
        "VALUES (?, ?, 'gto', ?, ?, 'call', 'fold', 'now', ?, 'facing_bet', ?, 0.05, 0.95)",
        [(hid, seq, st, pid, note, g) for seq, st, g, note in rows])  # fmt: skip
    conn.commit()
    conn.close()
    api = TestClient(create_app(path, tmp_path / "solves"))
    s = api.get(f"/api/review/{pid}/ev?source=gto").json()
    assert (s["graded"], s["blunders"], s["mixed"], s["best"]) == (2, 1, 1, 0)
    assert s["blunder_rate"] == 0.5 and s["blunders_per_100"] == 100.0
    assert [g["street"] for g in s["by_street"]] == ["flop", "river"]
    assert s["by_street"][0] == {"street": "flop", "n": 1, "graded": 1, "best": 0, "mixed": 0,
                                 "blunders": 1}  # fmt: skip
    assert s["top_hands"] == [{"hand_id": hid, "hand_no": s["top_hands"][0]["hand_no"],
                               "blunders": 1, "decisions": 2}]  # fmt: skip
    assert s["compare"]["gto"]["graded"] == 2
    assert s["unmodeled"] == {"decisions": 1, "blunders": 1}
    both = api.get(f"/api/review/{pid}/ev?source=gto&include_unmodeled=true").json()
    assert both["graded"] == 3 and both["blunders"] == 2
    r = CliRunner().invoke(app, ["ev", "summary", pid, "--source", "gto", "--db", str(path)])
    assert r.exit_code == 0 and "off-model" in r.output, r.output
