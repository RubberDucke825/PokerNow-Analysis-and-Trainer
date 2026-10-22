"""CLI smoke tests: `pn spot --range`, `pn solve new|run`, `pn library fill`
(solver binaries replaced by tests/fixtures/fake_solver.py)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn import ranges
from pn.cli.main import app

FIXTURES = Path(__file__).parent / "fixtures"
FAKE = FIXTURES / "fake_solver.py"


@pytest.fixture(autouse=True)
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich wraps at the detected terminal width; keep asserted lines on one line."""
    from rich.console import Console

    from pn.cli import solve_cmds

    monkeypatch.setattr(solve_cmds, "console", Console(width=200))


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_texas: Path) -> Path:
    monkeypatch.setenv("PN_SOLVE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("PN_LIBRARY_DIR", str(tmp_path / "library"))
    return tmp_path


@pytest.fixture
def db(tmp_path: Path) -> Path:
    from pn.ingest import db as ingest_db

    hands = [h for name in ("hands_core.json", "hands_special.json")
             for h in json.loads((FIXTURES / name).read_text())]  # fmt: skip
    path = tmp_path / "pn.sqlite"
    ingest_db.load(hands, path)
    return path


def _run(*args: str):
    return CliRunner().invoke(app, list(args))


def test_spot_gto_and_range(db: Path, tmp_path: Path) -> None:
    out = tmp_path / "s.toml"
    r = _run("spot", "c02_cbet33_fold", "--hero", "pC", "--db", str(db), "--range", "pF=QQ+,AKs",
             "--out", str(out))  # fmt: skip
    assert r.exit_code == 0, r.output
    cfg = tomllib.loads(out.read_text())
    assert ranges.parse(cfg["players"][0]["range"]) == {
        c: 1.0 for c in ranges.parse("QQ+,AKs") if "Kd" not in c}  # fmt: skip
    assert "GTO" in out.read_text().splitlines()[0]
    assert all("lock" not in p for p in cfg["players"])
    gto = _run("spot", "c02_cbet33_fold", "--hero", "pC", "--db", str(db))
    assert gto.exit_code == 0 and "range=pc:call BB vs CO" in gto.output
    assert _run("spot", "c02_cbet33_fold", "--db", str(db), "--source", "gto").exit_code != 0


def test_solve_new_and_run(fake_env: Path) -> None:
    out = fake_env / "new.toml"
    r = _run("solve", "new", "--seats", "btn,bb", "--board", "Kd2cKh", "--line", "srp",
             "--hero", "BTN", "--range", "BB=AA,KK", "--out", str(out))  # fmt: skip
    assert r.exit_code == 0, r.output
    cfg = tomllib.loads(out.read_text())
    assert cfg["game"]["street"] == "flop"
    assert ranges.parse(cfg["players"][0]["range"]) == {
        c: 1.0 for c in ranges.parse("AA,KK") if "Kd" not in c and "Kh" not in c}  # fmt: skip
    run = _run("solve", "run", str(out))
    assert run.exit_code == 0, run.output
    path = Path(run.output.strip().splitlines()[-1])
    assert path.exists() and path.parent == fake_env / "cache"
    assert "backend texas" in run.output
    rev = _run("solve", "run", str(out), "--preset", "review")
    assert rev.exit_code == 0, rev.output
    rev_doc = Path(rev.output.strip().splitlines()[-1])  # <key>.json.gz (Plan 17)
    rev_toml = (rev_doc.parent / f"{rev_doc.name.split('.')[0]}.toml").read_text()
    assert "max_bets_per_street = 2" in rev_toml and "time_limit_sec = 120" in rev_toml
    tr = _run("solve", "run", str(out), "--preset", "trainer")
    assert tr.exit_code == 0 and "backend texas" in tr.output  # heads-up postflop
    assert _run("solve", "run", str(out), "--preset", "turbo").exit_code == 1
    assert _run("solve", "new", "--seats", "BTN,BB", "--board", "Kd2cKh").exit_code == 1  # no line
    pre = _run("solve", "new", "--seats", "LJ,HJ,CO,BTN,SB,BB", "--depth", "200")
    assert pre.exit_code == 0 and 'street = "preflop"' in pre.output


def test_solve_run_progress(fake_env: Path) -> None:
    out = fake_env / "p.toml"
    assert _run("solve", "new", "--seats", "BTN,BB", "--board", "Kd2cKh", "--line", "srp",
                "--out", str(out)).exit_code == 0  # fmt: skip
    quiet = _run("solve", "run", str(out), "--preset", "trainer", "--no-progress")
    assert quiet.exit_code == 0 and "iter" not in quiet.stderr
    assert "exploitability" in quiet.stdout  # the result line carries the document's exploitability
    loud = _run("solve", "run", str(out), "--preset", "review", "--progress")
    assert loud.exit_code == 0, loud.output
    lines = [x for x in loud.stderr.splitlines() if x.startswith("iter")]
    assert lines and all("% pot" in x for x in lines)  # one line per print interval off a terminal
    cached = _run("solve", "run", str(out), "--preset", "review", "--progress")
    assert cached.exit_code == 0 and "iter" not in cached.stderr  # a cache hit runs no solver
